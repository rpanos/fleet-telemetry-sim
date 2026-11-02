#!/bin/bash
# EC2 user data: runs once, as root, on first boot. Clones the public repo and hands off to setup.sh.
set -euxo pipefail
apt-get update
apt-get install -y git
git clone https://github.com/rpanos/fleet-telemetry-sim.git /opt/fleet-telemetry-sim
bash /opt/fleet-telemetry-sim/deploy/setup.sh > /var/log/fleet-setup.log 2>&1
