"""Minimal checkpoint-compatible OSNet-AIN x1.0 inference architecture.

Adapted from Torchreid's ``torchreid/models/osnet_ain.py`` at commit
f6ba78eceb38712266318e8c7eefde174af15606.

Copyright (c) 2018 Kaiyang Zhou. Licensed under the MIT License; see
``licenses/TORCHREID_LICENSE.txt``. Only the layers needed to construct and
run OSNet-AIN x1.0, plus strict local checkpoint loading, are retained here.
No download or pretrained-weight helper is included.
"""

from collections.abc import Mapping
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F


UPSTREAM_COMMIT = "f6ba78eceb38712266318e8c7eefde174af15606"
SUPPORTED_STATE_DICT_PREFIXES = ("module.model.", "module.", "model.")
SUPPORTED_WRAPPER_KEYS = ("state_dict", "model")


class CheckpointError(RuntimeError):
    """A checkpoint cannot be loaded safely into OSNet-AIN x1.0."""


class ConvLayer(nn.Module):
    def __init__(
        self,
        in_channels,
        out_channels,
        kernel_size,
        stride=1,
        padding=0,
        groups=1,
        IN=False,
    ):
        super().__init__()
        self.conv = nn.Conv2d(
            in_channels,
            out_channels,
            kernel_size,
            stride=stride,
            padding=padding,
            bias=False,
            groups=groups,
        )
        if IN:
            self.bn = nn.InstanceNorm2d(out_channels, affine=True)
        else:
            self.bn = nn.BatchNorm2d(out_channels)
        self.relu = nn.ReLU()

    def forward(self, x):
        return self.relu(self.bn(self.conv(x)))


class Conv1x1(nn.Module):
    def __init__(self, in_channels, out_channels, stride=1, groups=1):
        super().__init__()
        self.conv = nn.Conv2d(
            in_channels,
            out_channels,
            1,
            stride=stride,
            padding=0,
            bias=False,
            groups=groups,
        )
        self.bn = nn.BatchNorm2d(out_channels)
        self.relu = nn.ReLU()

    def forward(self, x):
        return self.relu(self.bn(self.conv(x)))


class Conv1x1Linear(nn.Module):
    def __init__(self, in_channels, out_channels, stride=1, bn=True):
        super().__init__()
        self.conv = nn.Conv2d(
            in_channels,
            out_channels,
            1,
            stride=stride,
            padding=0,
            bias=False,
        )
        self.bn = nn.BatchNorm2d(out_channels) if bn else None

    def forward(self, x):
        x = self.conv(x)
        return self.bn(x) if self.bn is not None else x


class LightConv3x3(nn.Module):
    def __init__(self, in_channels, out_channels):
        super().__init__()
        self.conv1 = nn.Conv2d(
            in_channels, out_channels, 1, stride=1, padding=0, bias=False
        )
        self.conv2 = nn.Conv2d(
            out_channels,
            out_channels,
            3,
            stride=1,
            padding=1,
            bias=False,
            groups=out_channels,
        )
        self.bn = nn.BatchNorm2d(out_channels)
        self.relu = nn.ReLU()

    def forward(self, x):
        return self.relu(self.bn(self.conv2(self.conv1(x))))


class LightConvStream(nn.Module):
    def __init__(self, in_channels, out_channels, depth):
        super().__init__()
        if depth < 1:
            raise ValueError("depth must be at least one")
        layers = [LightConv3x3(in_channels, out_channels)]
        layers.extend(
            LightConv3x3(out_channels, out_channels)
            for _ in range(depth - 1)
        )
        self.layers = nn.Sequential(*layers)

    def forward(self, x):
        return self.layers(x)


class ChannelGate(nn.Module):
    def __init__(
        self,
        in_channels,
        num_gates=None,
        return_gates=False,
        gate_activation="sigmoid",
        reduction=16,
        layer_norm=False,
    ):
        super().__init__()
        if num_gates is None:
            num_gates = in_channels
        self.return_gates = return_gates
        self.global_avgpool = nn.AdaptiveAvgPool2d(1)
        self.fc1 = nn.Conv2d(
            in_channels,
            in_channels // reduction,
            kernel_size=1,
            bias=True,
            padding=0,
        )
        self.norm1 = (
            nn.LayerNorm((in_channels // reduction, 1, 1))
            if layer_norm
            else None
        )
        self.relu = nn.ReLU()
        self.fc2 = nn.Conv2d(
            in_channels // reduction,
            num_gates,
            kernel_size=1,
            bias=True,
            padding=0,
        )
        activations = {
            "sigmoid": nn.Sigmoid,
            "relu": nn.ReLU,
            "linear": lambda: None,
        }
        if gate_activation not in activations:
            raise RuntimeError(f"Unknown gate activation: {gate_activation}")
        self.gate_activation = activations[gate_activation]()

    def forward(self, x):
        inputs = x
        x = self.fc1(self.global_avgpool(x))
        if self.norm1 is not None:
            x = self.norm1(x)
        x = self.fc2(self.relu(x))
        if self.gate_activation is not None:
            x = self.gate_activation(x)
        return x if self.return_gates else inputs * x


class OSBlock(nn.Module):
    def __init__(self, in_channels, out_channels, reduction=4, T=4, **kwargs):
        super().__init__()
        if T < 1 or out_channels < reduction or out_channels % reduction:
            raise ValueError("invalid OSBlock dimensions")
        mid_channels = out_channels // reduction
        self.conv1 = Conv1x1(in_channels, mid_channels)
        self.conv2 = nn.ModuleList(
            LightConvStream(mid_channels, mid_channels, depth)
            for depth in range(1, T + 1)
        )
        self.gate = ChannelGate(mid_channels)
        self.conv3 = Conv1x1Linear(mid_channels, out_channels)
        self.downsample = (
            Conv1x1Linear(in_channels, out_channels)
            if in_channels != out_channels
            else None
        )

    def forward(self, x):
        identity = x
        x1 = self.conv1(x)
        x2 = sum(self.gate(stream(x1)) for stream in self.conv2)
        x3 = self.conv3(x2)
        if self.downsample is not None:
            identity = self.downsample(identity)
        return F.relu(x3 + identity)


class OSBlockINin(nn.Module):
    def __init__(self, in_channels, out_channels, reduction=4, T=4, **kwargs):
        super().__init__()
        if T < 1 or out_channels < reduction or out_channels % reduction:
            raise ValueError("invalid OSBlockINin dimensions")
        mid_channels = out_channels // reduction
        self.conv1 = Conv1x1(in_channels, mid_channels)
        self.conv2 = nn.ModuleList(
            LightConvStream(mid_channels, mid_channels, depth)
            for depth in range(1, T + 1)
        )
        self.gate = ChannelGate(mid_channels)
        self.conv3 = Conv1x1Linear(mid_channels, out_channels, bn=False)
        self.downsample = (
            Conv1x1Linear(in_channels, out_channels)
            if in_channels != out_channels
            else None
        )
        self.IN = nn.InstanceNorm2d(out_channels, affine=True)

    def forward(self, x):
        identity = x
        x1 = self.conv1(x)
        x2 = sum(self.gate(stream(x1)) for stream in self.conv2)
        x3 = self.IN(self.conv3(x2))
        if self.downsample is not None:
            identity = self.downsample(identity)
        return F.relu(x3 + identity)


class OSNet(nn.Module):
    """Inference-compatible Omni-Scale Network."""

    def __init__(
        self,
        num_classes,
        blocks,
        layers,
        channels,
        feature_dim=512,
        conv1_IN=False,
        include_classifier=True,
    ):
        super().__init__()
        if len(blocks) != len(layers) or len(blocks) != len(channels) - 1:
            raise ValueError("inconsistent OSNet stage configuration")
        self.feature_dim = feature_dim
        self.conv1 = ConvLayer(
            3, channels[0], 7, stride=2, padding=3, IN=conv1_IN
        )
        self.maxpool = nn.MaxPool2d(3, stride=2, padding=1)
        self.conv2 = self._make_layer(
            blocks[0], layers[0], channels[0], channels[1]
        )
        self.pool2 = nn.Sequential(
            Conv1x1(channels[1], channels[1]), nn.AvgPool2d(2, stride=2)
        )
        self.conv3 = self._make_layer(
            blocks[1], layers[1], channels[1], channels[2]
        )
        self.pool3 = nn.Sequential(
            Conv1x1(channels[2], channels[2]), nn.AvgPool2d(2, stride=2)
        )
        self.conv4 = self._make_layer(
            blocks[2], layers[2], channels[2], channels[3]
        )
        self.conv5 = Conv1x1(channels[3], channels[3])
        self.global_avgpool = nn.AdaptiveAvgPool2d(1)
        self.fc = self._construct_fc_layer(feature_dim, channels[3])
        if include_classifier:
            self.classifier = nn.Linear(feature_dim, num_classes)
        self._init_params()

    @staticmethod
    def _make_layer(blocks, layer_count, in_channels, out_channels):
        if layer_count != len(blocks):
            raise ValueError("block list does not match layer count")
        result = [blocks[0](in_channels, out_channels)]
        result.extend(
            block(out_channels, out_channels) for block in blocks[1:]
        )
        return nn.Sequential(*result)

    def _construct_fc_layer(self, output_dim, input_dim):
        self.feature_dim = output_dim
        return nn.Sequential(
            nn.Linear(input_dim, output_dim),
            nn.BatchNorm1d(output_dim),
            nn.ReLU(),
        )

    def _init_params(self):
        for module in self.modules():
            if isinstance(module, nn.Conv2d):
                nn.init.kaiming_normal_(
                    module.weight, mode="fan_out", nonlinearity="relu"
                )
                if module.bias is not None:
                    nn.init.constant_(module.bias, 0)
            elif isinstance(module, (nn.BatchNorm2d, nn.BatchNorm1d)):
                nn.init.constant_(module.weight, 1)
                nn.init.constant_(module.bias, 0)
            elif isinstance(module, nn.InstanceNorm2d):
                if module.affine:
                    nn.init.constant_(module.weight, 1)
                    nn.init.constant_(module.bias, 0)
            elif isinstance(module, nn.Linear):
                nn.init.normal_(module.weight, 0, 0.01)
                if module.bias is not None:
                    nn.init.constant_(module.bias, 0)

    def featuremaps(self, x):
        x = self.maxpool(self.conv1(x))
        x = self.pool2(self.conv2(x))
        x = self.pool3(self.conv3(x))
        return self.conv5(self.conv4(x))

    def forward(self, x):
        x = self.global_avgpool(self.featuremaps(x))
        x = x.view(x.size(0), -1)
        return self.fc(x) if self.fc is not None else x


def osnet_ain_x1_0(num_classes=1000, include_classifier=True):
    """Construct the pinned upstream OSNet-AIN x1.0 shape."""
    return OSNet(
        num_classes,
        blocks=[
            [OSBlockINin, OSBlockINin],
            [OSBlock, OSBlockINin],
            [OSBlockINin, OSBlock],
        ],
        layers=[2, 2, 2],
        channels=[64, 256, 384, 512],
        conv1_IN=True,
        include_classifier=include_classifier,
    )


def _is_tensor_state_dict(value):
    return (
        isinstance(value, Mapping)
        and bool(value)
        and all(isinstance(key, str) for key in value)
        and all(torch.is_tensor(tensor) for tensor in value.values())
    )


def _extract_state_dict(checkpoint):
    if _is_tensor_state_dict(checkpoint):
        return dict(checkpoint)
    if not isinstance(checkpoint, Mapping):
        raise CheckpointError("checkpoint root is not a state dictionary")
    found = [
        key
        for key in SUPPORTED_WRAPPER_KEYS
        if _is_tensor_state_dict(checkpoint.get(key))
    ]
    if len(found) != 1:
        raise CheckpointError(
            "checkpoint must contain exactly one supported tensor state "
            "dictionary (state_dict or model)"
        )
    return dict(checkpoint[found[0]])


def _strip_supported_prefix(state_dict):
    keys = list(state_dict)
    for prefix in SUPPORTED_STATE_DICT_PREFIXES:
        matches = [key.startswith(prefix) for key in keys]
        if all(matches):
            stripped = {key[len(prefix) :]: value for key, value in state_dict.items()}
            if len(stripped) != len(state_dict):
                raise CheckpointError("state-dict prefix removal caused key collisions")
            return stripped
        if any(matches):
            raise CheckpointError(
                f"state dict mixes prefixed and unprefixed keys for {prefix!r}"
            )
    return state_dict


def load_checkpoint_state(path):
    """Read a local checkpoint with PyTorch's restricted weights-only loader."""
    path = Path(path)
    if not path.is_file():
        raise CheckpointError(f"weights not found: {path}")
    try:
        checkpoint = torch.load(path, weights_only=True, map_location="cpu")
    except Exception as exc:
        raise CheckpointError(f"cannot safely load weights {path}: {exc}") from exc
    return _strip_supported_prefix(_extract_state_dict(checkpoint))


def load_state_dict_strict(model, state_dict):
    """Reject missing, unexpected, and shape-incompatible checkpoint entries."""
    try:
        incompatible = model.load_state_dict(state_dict, strict=False)
    except RuntimeError as exc:
        raise CheckpointError(f"checkpoint tensor shapes are incompatible: {exc}") from exc
    if incompatible.missing_keys or incompatible.unexpected_keys:
        raise CheckpointError(
            "checkpoint keys are incompatible; missing="
            f"{list(incompatible.missing_keys)!r}, unexpected="
            f"{list(incompatible.unexpected_keys)!r}"
        )


def load_osnet_ain_x1_0(path):
    """Build OSNet-AIN x1.0 and strictly load a user-supplied checkpoint."""
    state_dict = load_checkpoint_state(path)
    has_weight = "classifier.weight" in state_dict
    has_bias = "classifier.bias" in state_dict
    if has_weight != has_bias:
        raise CheckpointError(
            "checkpoint must contain both classifier.weight and classifier.bias, "
            "or neither"
        )
    if has_weight:
        weight = state_dict["classifier.weight"]
        bias = state_dict["classifier.bias"]
        if weight.ndim != 2 or weight.shape[1] != 512:
            raise CheckpointError("classifier.weight must have shape [classes, 512]")
        if bias.ndim != 1 or bias.shape[0] != weight.shape[0]:
            raise CheckpointError("classifier.bias shape does not match classifier.weight")
        num_classes = int(weight.shape[0])
    else:
        num_classes = 1
    model = osnet_ain_x1_0(
        num_classes=num_classes, include_classifier=has_weight
    )
    load_state_dict_strict(model, state_dict)
    model.eval()
    return model
