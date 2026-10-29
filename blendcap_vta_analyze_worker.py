"""Retired mixed worker. Production uses durable private-engine job IDs.
SPDX-License-Identifier: GPL-3.0-or-later
"""
STATUS_NAME="vta-analysis-status.json"
STOP_NAME="vta-analysis-stop"
if __name__=='__main__':
    raise SystemExit('This mixed worker is retired. Start a generation/analysis job through the versioned VTA engine interface.')
