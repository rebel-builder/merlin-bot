#!/usr/bin/env python3
"""Check ONVIF audio output capabilities on Amcrest camera."""

from config import CAMERA_IP, CAMERA_USER, CAMERA_PASS

try:
    from onvif import ONVIFCamera
except ImportError:
    print("onvif-zeep not installed, using raw SOAP")
    import requests

    # Raw SOAP query for audio outputs
    url = f"http://{CAMERA_IP}/onvif/media_service"
    soap = f"""<?xml version="1.0"?>
    <soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
                   xmlns:trt="http://www.onvif.org/ver10/media/wsdl"
                   xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
                   xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">
      <soap:Header>
        <wsse:Security>
          <wsse:UsernameToken>
            <wsse:Username>{CAMERA_USER}</wsse:Username>
            <wsse:Password>{CAMERA_PASS}</wsse:Password>
          </wsse:UsernameToken>
        </wsse:Security>
      </soap:Header>
      <soap:Body>
        <trt:GetProfiles/>
      </soap:Body>
    </soap:Envelope>"""

    r = requests.post(url, data=soap, headers={"Content-Type": "application/soap+xml"}, timeout=10)
    print(f"GetProfiles status: {r.status_code}")

    # Search for audio-related terms
    text = r.text
    for kw in ["Audio", "audio", "Speaker", "speaker", "Output", "Backchannel", "backchannel", "Decoder"]:
        if kw in text:
            # Find context
            idx = text.find(kw)
            snippet = text[max(0, idx-50):idx+100]
            print(f"  Found '{kw}': ...{snippet}...")

    # Also try GetAudioOutputs
    soap2 = soap.replace("GetProfiles", "GetAudioOutputs")
    r2 = requests.post(url, data=soap2, headers={"Content-Type": "application/soap+xml"}, timeout=10)
    print(f"\nGetAudioOutputs status: {r2.status_code}")
    print(f"Response: {r2.text[:500]}")

    # Try GetServiceCapabilities
    soap3 = soap.replace("GetProfiles", "GetServiceCapabilities")
    r3 = requests.post(url, data=soap3, headers={"Content-Type": "application/soap+xml"}, timeout=10)
    print(f"\nGetServiceCapabilities status: {r3.status_code}")
    for kw in ["Audio", "audio", "Speaker", "Backchannel", "backchannel"]:
        if kw in r3.text:
            idx = r3.text.find(kw)
            print(f"  Found '{kw}': ...{r3.text[max(0,idx-30):idx+80]}...")

    exit()

# If onvif-zeep is installed
cam = ONVIFCamera(CAMERA_IP, 80, CAMERA_USER, CAMERA_PASS)
media = cam.create_media_service()

print("=== Profiles ===")
profiles = media.GetProfiles()
for p in profiles:
    print(f"Profile: {p.Name}")
    if hasattr(p, "AudioOutputConfiguration") and p.AudioOutputConfiguration:
        print(f"  AudioOutput: {p.AudioOutputConfiguration}")
    if hasattr(p, "AudioEncoderConfiguration") and p.AudioEncoderConfiguration:
        print(f"  AudioEncoder: {p.AudioEncoderConfiguration}")
    if hasattr(p, "AudioSourceConfiguration") and p.AudioSourceConfiguration:
        print(f"  AudioSource: {p.AudioSourceConfiguration}")
    if hasattr(p, "AudioDecoderConfiguration") and p.AudioDecoderConfiguration:
        print(f"  AudioDecoder: {p.AudioDecoderConfiguration}")

print("\n=== Audio Outputs ===")
try:
    outputs = media.GetAudioOutputs()
    print(f"Audio outputs: {outputs}")
except Exception as e:
    print(f"GetAudioOutputs error: {e}")

print("\n=== Audio Sources ===")
try:
    sources = media.GetAudioSources()
    print(f"Audio sources: {sources}")
except Exception as e:
    print(f"GetAudioSources error: {e}")

print("\n=== Service Capabilities ===")
try:
    caps = media.GetServiceCapabilities()
    print(f"Capabilities: {caps}")
except Exception as e:
    print(f"Caps error: {e}")
