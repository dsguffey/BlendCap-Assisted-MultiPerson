"""Retired mixed worker. Production uses durable private-engine job IDs.
SPDX-License-Identifier: GPL-3.0-or-later
"""
if __package__:
    from .blendcap_vta_body_provider import bbox_for_backend,export_command,infer_reviewed_pose
else:
    from blendcap_vta_body_provider import bbox_for_backend,export_command,infer_reviewed_pose
if __name__=='__main__':
    raise SystemExit('This mixed worker is retired. Start a generation/analysis job through the versioned VTA engine interface.')
