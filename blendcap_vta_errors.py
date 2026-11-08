"""Stable actionable primary failures; detailed diagnostics stay in local logs.
SPDX-License-Identifier: GPL-3.0-or-later
"""
MESSAGES={
    'RUNTIME_MISSING':'MoCap runtime is missing. Run MoCap setup or repair.',
    'RUNTIME_INVALID':'MoCap runtime configuration is invalid. Run repair.',
    'RUNTIME_UNAVAILABLE':'MoCap runtime is unavailable. Check setup and run repair.',
    'ENGINE_UNAVAILABLE':'MoCap engine is unavailable. Run setup or repair.',
    'VERSION_MISMATCH':'MoCap components are incompatible. Install the matching MoCap package.',
    'MODEL_MISSING':'A required model asset is missing. Run repair or finish model acquisition.',
    'CUDA_UNAVAILABLE':'The supported NVIDIA CUDA runtime is unavailable. Check the GPU driver and runtime.',
    'OUT_OF_MEMORY':'GPU memory is insufficient. Close other GPU applications and retry a smaller range.',
    'PROVIDER_FAILED':'The AI provider stopped unexpectedly. See the job log, then retry explicitly.',
    'PROVIDER_TIMEOUT':'The AI provider stopped reporting progress. See the job log, then retry explicitly.',
    'MALFORMED_RESULT':'The AI result is invalid. Prior animation was retained; inspect the job log.',
    'STALE_RESULT':'The result no longer matches the project. Refresh and generate explicitly.',
    'CONFLICT':'Project state changed. Refresh and confirm the operation again.',
    'RECOVERY_REQUIRED':'MoCap recovery is pending. Use explicit reconciliation or job recovery.',
    'WORKER_LOST':'The job was interrupted. Recover or discard it explicitly; no automatic rerun.',
    'DISK_FULL':'The disk is full. Free space and retry explicitly.',
    'INVALID_PATH':'A MoCap path is unsafe or unavailable. Check the project location and runtime setup.',
    'NATIVE_APPLICATION_FAILED':'Blender could not apply the result. Prior animation was retained; see Details.',
}

def primary(code):return MESSAGES.get(code,'MoCap could not complete the operation. See Details and the job log.')
