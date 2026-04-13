#!/usr/bin/env python3
# fix_gemma4_tensors.py — v1.0.0
# Fix MLX-fused Gemma 4 safetensors so llama.cpp can convert them to GGUF.
# https://github.com/ggml-org/llama.cpp
"""
fix_gemma4_tensors.py
=====================

Fix MLX-fused Gemma 4 safetensors so llama.cpp's convert_hf_to_gguf.py can
convert them to GGUF format.

THE PROBLEM
-----------
When you fine-tune Gemma 4 with MLX-LM and fuse the LoRA weights back into
the base model (``mlx_lm.fuse``), MLX stores the MoE (Mixture of Experts)
expert tensors under its own naming convention:

    language_model.model.layers.X.experts.switch_glu.gate_proj.weight
    language_model.model.layers.X.experts.switch_glu.up_proj.weight
    language_model.model.layers.X.experts.switch_glu.down_proj.weight

The llama.cpp HuggingFace converter (``convert_hf_to_gguf.py``) expects a
different layout — the ``switch_glu`` infix is absent, and gate + up are
concatenated into a single fused projection:

    language_model.model.layers.X.experts.gate_up_proj.weight   ← gate + up fused
    language_model.model.layers.X.experts.down_proj.weight

Two transformations are required:
  1. For MoE expert layers:
       a. Remove ``.switch_glu`` from the tensor name.
       b. Concatenate ``gate_proj`` and ``up_proj`` along dim 1 into a single
          ``gate_up_proj`` tensor (SwiGLU fused projection layout).
       c. Rename ``down_proj`` → ``down_proj`` (name fix only; values unchanged).
  2. All other tensors (attention, norms, embeddings, etc.) pass through
     unchanged — names and values are preserved as-is.

IMPORTANT: Do NOT strip the ``language_model.`` prefix. The llama.cpp Gemma 4
handler handles that internally during conversion. Stripping it here will
break recognition of attention and norm tensors.

CROSS-SHARD HANDLING
--------------------
Gate and up tensors for the same expert layer may live in different safetensors
shards. This script handles that case by opening the second shard on demand
without loading the entire model into memory at once.

USAGE
-----
Basic (writes fixed tensors to ./gemma4-fixed):
    python fix_gemma4_tensors.py /path/to/mlx-fused-model

Custom output directory:
    python fix_gemma4_tensors.py /path/to/mlx-fused-model --out /path/to/output

Full pipeline example:
    # 1. Fine-tune with MLX-LM
    mlx_lm.lora --model google/gemma-4-27b-it --data ./data --train

    # 2. Fuse LoRA weights into base model
    mlx_lm.fuse --model google/gemma-4-27b-it --adapter-path adapters/ \\
                 --save-path ./gemma4-fused

    # 3. Fix tensor names (this script)
    python fix_gemma4_tensors.py ./gemma4-fused --out ./gemma4-fixed

    # 4. Convert to GGUF with llama.cpp
    python llama.cpp/convert_hf_to_gguf.py ./gemma4-fixed \\
           --outfile gemma4.gguf --outtype q8_0

REQUIREMENTS
------------
    pip install safetensors torch

TENSOR SHAPE NOTES
------------------
- Gate+up concatenation happens along dim 1.
- Gemma 4 MoE expert shape: [num_experts, hidden_dim, ffn_dim].
- Fused gate_up_proj shape:  [num_experts, 2*hidden_dim, ffn_dim].
"""

__version__ = "1.0.0"

import argparse
import gc
import json
import os
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

import torch
from safetensors import safe_open
from safetensors.torch import save_file


def parse_args():
    parser = argparse.ArgumentParser(
        description="Fix MLX-fused Gemma 4 safetensors for llama.cpp GGUF conversion.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "src",
        type=Path,
        help="Path to the MLX-fused model directory (must contain model.safetensors.index.json)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output directory for fixed tensors (default: <src>-fixed)",
    )
    return parser.parse_args()


def copy_config_files(src_dir: Path, dst_dir: Path):
    """Copy all non-tensor config files so the llama.cpp converter has what it needs."""
    config_files = [
        "config.json",
        "generation_config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "chat_template.jinja",
        "special_tokens_map.json",
    ]
    for fname in config_files:
        src = src_dir / fname
        dst = dst_dir / fname
        if src.exists():
            shutil.copy2(src, dst)
            print(f"  Copied {fname}")


def load_index(src_dir: Path) -> dict:
    """Load the safetensors shard index and return its full contents as a dict."""
    index_path = src_dir / "model.safetensors.index.json"
    if not index_path.exists():
        sys.exit(
            f"ERROR: {index_path} not found.\n"
            "Only sharded models (model.safetensors.index.json) are supported.\n"
            "Single-file models are not yet handled."
        )
    with open(index_path) as f:
        return json.load(f)


def find_expert_tensor_locations(weight_map: dict) -> tuple[dict, dict, dict]:
    """
    Scan the weight map for MoE switch_glu tensors and return their shard locations.

    Returns three dicts, each mapping layer_id -> (shard_filename, tensor_name):
      gate_locs  — switch_glu.gate_proj tensors
      up_locs    — switch_glu.up_proj tensors
      down_locs  — switch_glu.down_proj tensors
    """
    gate_locs = {}
    up_locs = {}
    down_locs = {}

    for tensor_name, shard_file in weight_map.items():
        m = re.search(r"layers\.(\d+)", tensor_name)
        if m is None:
            continue
        layer_id = int(m.group(1))

        if "experts.switch_glu.gate_proj" in tensor_name:
            gate_locs[layer_id] = (shard_file, tensor_name)
        elif "experts.switch_glu.up_proj" in tensor_name:
            up_locs[layer_id] = (shard_file, tensor_name)
        elif "experts.switch_glu.down_proj" in tensor_name:
            down_locs[layer_id] = (shard_file, tensor_name)

    return gate_locs, up_locs, down_locs


def process_shard(
    src_dir: Path,
    dst_dir: Path,
    shard_file: str,
    out_shard_name: str,
    tensor_names_in_shard: list,
    gate_locs: dict,
    up_locs: dict,
    new_weight_map: dict,
    processed_tensors: set,
):
    """
    Process one input shard and write one output shard.

    Handles three cases for MoE expert tensors:
      - gate_proj: fuse with up_proj -> gate_up_proj (may cross shard boundaries)
      - up_proj:   handled together with gate; skip if already fused
      - down_proj: strip the switch_glu infix from the name

    All other tensors pass through with names and values unchanged.
    Mutates new_weight_map and processed_tensors in place.
    """
    output_tensors = {}

    with safe_open(src_dir / shard_file, framework="pt") as sf:
        for orig_name in sorted(tensor_names_in_shard):
            # Skip tensors already handled during a previous shard's fusion pass
            if orig_name in processed_tensors:
                continue

            data = sf.get_tensor(orig_name)

            # ----------------------------------------------------------------
            # Case 1: gate_proj — fuse gate + up into a single gate_up_proj
            # ----------------------------------------------------------------
            if "experts.switch_glu.gate_proj" in orig_name:
                m = re.search(r"layers\.(\d+)", orig_name)
                layer_id = int(m.group(1))
                up_shard_file, up_tensor_name = up_locs[layer_id]

                if up_shard_file == shard_file:
                    # Both halves are in this shard — fast path
                    up_data = sf.get_tensor(up_tensor_name)
                else:
                    # up_proj is in a different shard — open it on demand
                    with safe_open(src_dir / up_shard_file, framework="pt") as sf2:
                        up_data = sf2.get_tensor(up_tensor_name)

                print(
                    f"  Fusing layer {layer_id}: "
                    f"gate {list(data.shape)} + up {list(up_data.shape)}"
                )
                # SwiGLU layout: [experts, gate_channels + up_channels, in_channels]
                fused = torch.cat([data, up_data], dim=1)
                print(f"  -> gate_up_proj {list(fused.shape)}")

                fused_name = orig_name.replace(
                    "experts.switch_glu.gate_proj", "experts.gate_up_proj"
                )
                output_tensors[fused_name] = fused
                new_weight_map[fused_name] = out_shard_name
                processed_tensors.add(orig_name)
                processed_tensors.add(up_tensor_name)

                del fused, up_data
                continue

            # ----------------------------------------------------------------
            # Case 2: up_proj — may need to drive the fusion if gate_proj
            # hasn't been seen yet (e.g. gate lives in a later shard)
            # ----------------------------------------------------------------
            elif "experts.switch_glu.up_proj" in orig_name:
                if orig_name in processed_tensors:
                    # Already fused from the gate side — nothing to do
                    continue

                m = re.search(r"layers\.(\d+)", orig_name)
                layer_id = int(m.group(1))
                gate_shard_file, gate_tensor_name = gate_locs[layer_id]

                if gate_tensor_name in processed_tensors:
                    # Gate was fused in a prior shard — mark up as done and skip
                    processed_tensors.add(orig_name)
                    continue

                # Gate hasn't been seen yet — drive the fusion from the up side
                with safe_open(src_dir / gate_shard_file, framework="pt") as sf2:
                    gate_data = sf2.get_tensor(gate_tensor_name)

                print(
                    f"  Fusing layer {layer_id} (from up side): "
                    f"gate {list(gate_data.shape)} + up {list(data.shape)}"
                )
                fused = torch.cat([gate_data, data], dim=1)
                print(f"  -> gate_up_proj {list(fused.shape)}")

                fused_name = orig_name.replace(
                    "experts.switch_glu.up_proj", "experts.gate_up_proj"
                )
                output_tensors[fused_name] = fused
                new_weight_map[fused_name] = out_shard_name
                processed_tensors.add(orig_name)
                processed_tensors.add(gate_tensor_name)

                del fused, gate_data
                continue

            # ----------------------------------------------------------------
            # Case 3: down_proj — strip the switch_glu infix, values unchanged
            # ----------------------------------------------------------------
            elif "experts.switch_glu.down_proj" in orig_name:
                new_name = orig_name.replace(
                    "experts.switch_glu.down_proj", "experts.down_proj"
                )
                output_tensors[new_name] = data
                new_weight_map[new_name] = out_shard_name
                processed_tensors.add(orig_name)
                continue

            # ----------------------------------------------------------------
            # Default: all other tensors pass through unchanged
            # ----------------------------------------------------------------
            output_tensors[orig_name] = data
            new_weight_map[orig_name] = out_shard_name
            processed_tensors.add(orig_name)

    # Write this shard's output file
    if output_tensors:
        out_path = dst_dir / out_shard_name
        print(f"  Writing {out_shard_name} ({len(output_tensors)} tensors)...")
        save_file(output_tensors, str(out_path))
        print(f"  Size: {out_path.stat().st_size / 1e9:.2f} GB")

    # Free memory before moving on to the next shard
    del output_tensors
    gc.collect()


def verify_output(new_weight_map: dict, dst_dir: Path):
    """
    Sanity-check the output weight map.

    Warns if any switch_glu tensors slipped through (indicates a bug),
    then prints a sample of output tensor names for a quick visual check.
    """
    print("\n--- Verification ---")

    # Any remaining switch_glu names mean the fix failed for those tensors
    bad_switch = [n for n in new_weight_map if "switch_glu" in n]
    if bad_switch:
        print(f"WARNING: {len(bad_switch)} tensors still contain 'switch_glu':")
        for n in bad_switch[:10]:
            print(f"  {n}")
        if len(bad_switch) > 10:
            print(f"  ... and {len(bad_switch) - 10} more")
    else:
        print("OK: No 'switch_glu' tensors remaining.")

    # Print a sample so you can eyeball that names look right
    print("\nSample output tensor names (first 20):")
    for name in sorted(new_weight_map.keys())[:20]:
        print(f"  {name}")


def main():
    args = parse_args()
    src_dir: Path = args.src.resolve()

    # Default output dir: <src>-fixed (next to the source, not /tmp)
    if args.out is None:
        dst_dir = src_dir.parent / (src_dir.name + "-fixed")
    else:
        dst_dir = args.out.resolve()

    if not src_dir.exists():
        sys.exit(f"ERROR: Source directory not found: {src_dir}")

    dst_dir.mkdir(parents=True, exist_ok=True)
    print(f"Source : {src_dir}")
    print(f"Output : {dst_dir}\n")

    # --- Step 1: Copy config / tokenizer files ---
    # llama.cpp's converter needs config.json and tokenizer files alongside the tensors
    print("Copying config files...")
    copy_config_files(src_dir, dst_dir)

    # Print model identity so you can confirm you're operating on the right checkpoint
    config_path = dst_dir / "config.json"
    with open(config_path) as f:
        config = json.load(f)
    print(f"\nArchitecture : {config.get('architectures', ['?'])}")
    print(f"Model type   : {config.get('model_type', '?')}")

    # --- Step 2: Load shard index ---
    index = load_index(src_dir)
    weight_map = index["weight_map"]
    print(f"\nTotal source tensors: {len(weight_map)}")

    # Group tensor names by which shard file they live in
    shard_tensors: dict[str, list] = defaultdict(list)
    for tensor_name, shard_file in weight_map.items():
        shard_tensors[shard_file].append(tensor_name)

    # --- Step 3: Pre-scan — locate all switch_glu tensors before processing ---
    # We need the full location map upfront so cross-shard fusions can find both halves
    gate_locs, up_locs, down_locs = find_expert_tensor_locations(weight_map)
    print(f"Expert layers found — gate: {sorted(gate_locs.keys())}")
    print(f"Expert layers found — up  : {sorted(up_locs.keys())}")
    print(f"Expert layers found — down: {sorted(down_locs.keys())}")

    # Every gate layer must have a matching up layer or the model is corrupt
    assert set(gate_locs.keys()) == set(up_locs.keys()), (
        "FATAL: gate_proj and up_proj exist for different layer sets. "
        "The model may be corrupt or in an unexpected format."
    )

    # --- Step 4: Process each shard ---
    processed_tensors: set = set()
    new_weight_map: dict = {}
    sorted_shards = sorted(shard_tensors.keys())
    shard_count = len(sorted_shards)

    for idx, shard_file in enumerate(sorted_shards, start=1):
        out_shard_name = f"model-{idx:05d}-of-{shard_count:05d}.safetensors"
        print(f"\n[{idx}/{shard_count}] {shard_file} -> {out_shard_name}")

        process_shard(
            src_dir=src_dir,
            dst_dir=dst_dir,
            shard_file=shard_file,
            out_shard_name=out_shard_name,
            tensor_names_in_shard=shard_tensors[shard_file],
            gate_locs=gate_locs,
            up_locs=up_locs,
            new_weight_map=new_weight_map,
            processed_tensors=processed_tensors,
        )

    # --- Step 5: Write the updated shard index ---
    new_index = {
        "metadata": index.get("metadata", {}),
        "weight_map": new_weight_map,
    }
    index_out = dst_dir / "model.safetensors.index.json"
    with open(index_out, "w") as f:
        json.dump(new_index, f, indent=2)
    print(f"\nWrote index: {index_out}")
    print(f"Total output tensors: {len(new_weight_map)}")

    # --- Step 6: Verify ---
    verify_output(new_weight_map, dst_dir)

    print(f"\n=== DONE ===")
    print(f"Fixed model ready at: {dst_dir}")
    print("\nNext step — convert to GGUF with llama.cpp:")
    print(
        f"  python llama.cpp/convert_hf_to_gguf.py {dst_dir} "
        "--outfile gemma4.gguf --outtype q8_0"
    )


if __name__ == "__main__":
    main()
