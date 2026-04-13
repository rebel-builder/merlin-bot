#!/bin/bash
# Install Merlin systemd services on Pi
# Run as: sudo bash install.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Installing Merlin systemd services..."

# Copy service files
cp "$SCRIPT_DIR/merlin-go2rtc.service" /etc/systemd/system/
cp "$SCRIPT_DIR/merlin-senses.service" /etc/systemd/system/

# Reload systemd
systemctl daemon-reload

# Enable services (start on boot)
systemctl enable merlin-go2rtc.service
systemctl enable merlin-senses.service

# Start services
systemctl start merlin-go2rtc.service
sleep 2
systemctl start merlin-senses.service

echo "Services installed and started."
echo "  merlin-go2rtc: $(systemctl is-active merlin-go2rtc.service)"
echo "  merlin-senses: $(systemctl is-active merlin-senses.service)"
echo ""
echo "Useful commands:"
echo "  sudo systemctl status merlin-senses"
echo "  sudo journalctl -u merlin-senses -f"
echo "  sudo systemctl restart merlin-senses"
