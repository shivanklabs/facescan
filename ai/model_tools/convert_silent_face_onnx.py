"""Convert Silent-Face Anti-Spoofing PyTorch models to ONNX.

Upstream: https://github.com/minivision-ai/Silent-Face-Anti-Spoofing
Commit: b6d5f04ad78778917853b25c778acef6d5626d15 (2020-08-05)
PyTorch version: 2.6.0
ONNX opset: 17

Upstream .pth SHA-256 digests:
- 2.7_80x80_MiniFASNetV2.pth:
  a5eb02e1843f19b5386b953cc4c9f011c3f985d0ee2bb9819eea9a142099bec0
- 4_0_0_80x80_MiniFASNetV1SE.pth:
  84ee1d37d96894d5e82de5a57df044ef80a58be2b218b5ed7cdfd875ec2f5990

Exported ONNX SHA-256 digests:
- 2.7_80x80_MiniFASNetV2.onnx:
  39397b412ac2a3f39dde90cb45fb8525b8cf8018355ec2f1a402b24b01d9c7f8
- 4_0_0_80x80_MiniFASNetV1SE.onnx:
  4238cb5bc78f225644ed3f6078a46463884696e2d9fd5efb1cbfc15ef723ce06

Run once with:
uv run --with "torch==2.6.0" --with "onnx==1.17.0" python ai/model_tools/convert_silent_face_onnx.py
"""

import hashlib
import urllib.request
from collections import OrderedDict
from pathlib import Path

import onnxruntime as ort
import torch
from torch import nn

MODELS_DIR = Path(__file__).resolve().parents[1] / "models"
TMP_DIR = Path(__file__).resolve().parents[2] / "var" / "tmp"

BASE_URL = (
    "https://raw.githubusercontent.com/minivision-ai/Silent-Face-Anti-Spoofing/master/"
    "resources/anti_spoof_models/"
)
MODEL_FILES = (
    ("2.7_80x80_MiniFASNetV2.pth", "2.7_80x80_MiniFASNetV2.onnx", "MiniFASNetV2"),
    ("4_0_0_80x80_MiniFASNetV1SE.pth", "4_0_0_80x80_MiniFASNetV1SE.onnx", "MiniFASNetV1SE"),
)


# ---------------------------------------------------------------- architecture


class ConvBlock(nn.Module):
    """Convolution + BatchNorm + PReLU block."""

    def __init__(
        self,
        in_c: int,
        out_c: int,
        kernel: tuple[int, int] = (1, 1),
        stride: tuple[int, int] = (1, 1),
        padding: tuple[int, int] = (0, 0),
        groups: int = 1,
    ) -> None:
        super().__init__()
        self.conv = nn.Conv2d(
            in_c,
            out_c,
            kernel_size=kernel,
            groups=groups,
            stride=stride,
            padding=padding,
            bias=False,
        )
        self.bn = nn.BatchNorm2d(out_c)
        self.prelu = nn.PReLU(out_c)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.prelu(self.bn(self.conv(x)))


class LinearBlock(nn.Module):
    """Convolution + BatchNorm block (no activation)."""

    def __init__(
        self,
        in_c: int,
        out_c: int,
        kernel: tuple[int, int] = (1, 1),
        stride: tuple[int, int] = (1, 1),
        padding: tuple[int, int] = (0, 0),
        groups: int = 1,
    ) -> None:
        super().__init__()
        self.conv = nn.Conv2d(
            in_c,
            out_channels=out_c,
            kernel_size=kernel,
            groups=groups,
            stride=stride,
            padding=padding,
            bias=False,
        )
        self.bn = nn.BatchNorm2d(out_c)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.bn(self.conv(x))


class DepthWise(nn.Module):
    """Depthwise separable convolution."""

    def __init__(
        self,
        c1: tuple[int, int],
        c2: tuple[int, int],
        c3: tuple[int, int],
        residual: bool = False,
        kernel: tuple[int, int] = (3, 3),
        stride: tuple[int, int] = (2, 2),
        padding: tuple[int, int] = (1, 1),
        groups: int = 1,
    ) -> None:
        super().__init__()
        c1_in, c1_out = c1
        c2_in, c2_out = c2
        c3_in, c3_out = c3
        self.conv = ConvBlock(c1_in, out_c=c1_out, kernel=(1, 1), padding=(0, 0), stride=(1, 1))
        self.conv_dw = ConvBlock(
            c2_in, c2_out, groups=c2_in, kernel=kernel, padding=padding, stride=stride
        )
        self.project = LinearBlock(c3_in, c3_out, kernel=(1, 1), padding=(0, 0), stride=(1, 1))
        self.residual = residual

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        short_cut = x
        x = self.project(self.conv_dw(self.conv(x)))
        return short_cut + x if self.residual else x


class Residual(nn.Module):
    """Sequence of DepthWise blocks."""

    def __init__(
        self,
        c1: list[tuple[int, int]],
        c2: list[tuple[int, int]],
        c3: list[tuple[int, int]],
        num_block: int,
        groups: int,
        kernel: tuple[int, int] = (3, 3),
        stride: tuple[int, int] = (1, 1),
        padding: tuple[int, int] = (1, 1),
    ) -> None:
        super().__init__()
        modules: list[nn.Module] = []
        for i in range(num_block):
            modules.append(
                DepthWise(
                    c1[i],
                    c2[i],
                    c3[i],
                    residual=True,
                    kernel=kernel,
                    padding=padding,
                    stride=stride,
                    groups=groups,
                )
            )
        self.model = nn.Sequential(*modules)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.model(x)


class SEModule(nn.Module):
    """Squeeze-and-Excitation block."""

    def __init__(self, channels: int, reduction: int) -> None:
        super().__init__()
        self.avg_pool = nn.AdaptiveAvgPool2d(1)
        self.fc1 = nn.Conv2d(channels, channels // reduction, kernel_size=1, padding=0, bias=False)
        self.bn1 = nn.BatchNorm2d(channels // reduction)
        self.relu = nn.ReLU(inplace=True)
        self.fc2 = nn.Conv2d(channels // reduction, channels, kernel_size=1, padding=0, bias=False)
        self.bn2 = nn.BatchNorm2d(channels)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        module_input = x
        x = self.sigmoid(self.bn2(self.fc2(self.relu(self.bn1(self.fc1(self.avg_pool(x)))))))
        return module_input * x


class DepthWiseSE(nn.Module):
    """DepthWise block with Squeeze-and-Excitation."""

    def __init__(
        self,
        c1: tuple[int, int],
        c2: tuple[int, int],
        c3: tuple[int, int],
        residual: bool = False,
        kernel: tuple[int, int] = (3, 3),
        stride: tuple[int, int] = (2, 2),
        padding: tuple[int, int] = (1, 1),
        groups: int = 1,
        se_reduct: int = 8,
    ) -> None:
        super().__init__()
        c1_in, c1_out = c1
        c2_in, c2_out = c2
        c3_in, c3_out = c3
        self.conv = ConvBlock(c1_in, out_c=c1_out, kernel=(1, 1), padding=(0, 0), stride=(1, 1))
        self.conv_dw = ConvBlock(
            c2_in, c2_out, groups=c2_in, kernel=kernel, padding=padding, stride=stride
        )
        self.project = LinearBlock(c3_in, c3_out, kernel=(1, 1), padding=(0, 0), stride=(1, 1))
        self.residual = residual
        self.se_module = SEModule(c3_out, se_reduct)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        short_cut = x
        x = self.project(self.conv_dw(self.conv(x)))
        if self.residual:
            x = self.se_module(x)
            return short_cut + x
        return x


class ResidualSE(nn.Module):
    """Sequence of DepthWise blocks with final SE block."""

    def __init__(
        self,
        c1: list[tuple[int, int]],
        c2: list[tuple[int, int]],
        c3: list[tuple[int, int]],
        num_block: int,
        groups: int,
        kernel: tuple[int, int] = (3, 3),
        stride: tuple[int, int] = (1, 1),
        padding: tuple[int, int] = (1, 1),
        se_reduct: int = 4,
    ) -> None:
        super().__init__()
        modules: list[nn.Module] = []
        for i in range(num_block):
            if i == num_block - 1:
                modules.append(
                    DepthWiseSE(
                        c1[i],
                        c2[i],
                        c3[i],
                        residual=True,
                        kernel=kernel,
                        padding=padding,
                        stride=stride,
                        groups=groups,
                        se_reduct=se_reduct,
                    )
                )
            else:
                modules.append(
                    DepthWise(
                        c1[i],
                        c2[i],
                        c3[i],
                        residual=True,
                        kernel=kernel,
                        padding=padding,
                        stride=stride,
                        groups=groups,
                    )
                )
        self.model = nn.Sequential(*modules)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.model(x)


class MiniFASNet(nn.Module):
    """Base MiniFASNet architecture."""

    def __init__(
        self,
        keep: list[int],
        embedding_size: int = 128,
        conv6_kernel: tuple[int, int] = (5, 5),
        drop_p: float = 0.2,
        num_classes: int = 3,
        img_channel: int = 3,
    ) -> None:
        super().__init__()
        self.embedding_size = embedding_size
        self.conv1 = ConvBlock(img_channel, keep[0], kernel=(3, 3), stride=(2, 2), padding=(1, 1))
        self.conv2_dw = ConvBlock(
            keep[0], keep[1], kernel=(3, 3), stride=(1, 1), padding=(1, 1), groups=keep[1]
        )

        c1 = [(keep[1], keep[2])]
        c2 = [(keep[2], keep[3])]
        c3 = [(keep[3], keep[4])]
        self.conv_23 = DepthWise(
            c1[0], c2[0], c3[0], kernel=(3, 3), stride=(2, 2), padding=(1, 1), groups=keep[3]
        )

        c1 = [(keep[4], keep[5]), (keep[7], keep[8]), (keep[10], keep[11]), (keep[13], keep[14])]
        c2 = [(keep[5], keep[6]), (keep[8], keep[9]), (keep[11], keep[12]), (keep[14], keep[15])]
        c3 = [(keep[6], keep[7]), (keep[9], keep[10]), (keep[12], keep[13]), (keep[15], keep[16])]
        self.conv_3 = Residual(
            c1, c2, c3, num_block=4, groups=keep[4], kernel=(3, 3), stride=(1, 1), padding=(1, 1)
        )

        c1 = [(keep[16], keep[17])]
        c2 = [(keep[17], keep[18])]
        c3 = [(keep[18], keep[19])]
        self.conv_34 = DepthWise(
            c1[0], c2[0], c3[0], kernel=(3, 3), stride=(2, 2), padding=(1, 1), groups=keep[19]
        )

        c1 = [
            (keep[19], keep[20]),
            (keep[22], keep[23]),
            (keep[25], keep[26]),
            (keep[28], keep[29]),
            (keep[31], keep[32]),
            (keep[34], keep[35]),
        ]
        c2 = [
            (keep[20], keep[21]),
            (keep[23], keep[24]),
            (keep[26], keep[27]),
            (keep[29], keep[30]),
            (keep[32], keep[33]),
            (keep[35], keep[36]),
        ]
        c3 = [
            (keep[21], keep[22]),
            (keep[24], keep[25]),
            (keep[27], keep[28]),
            (keep[30], keep[31]),
            (keep[33], keep[34]),
            (keep[36], keep[37]),
        ]
        self.conv_4 = Residual(
            c1, c2, c3, num_block=6, groups=keep[19], kernel=(3, 3), stride=(1, 1), padding=(1, 1)
        )

        c1 = [(keep[37], keep[38])]
        c2 = [(keep[38], keep[39])]
        c3 = [(keep[39], keep[40])]
        self.conv_45 = DepthWise(
            c1[0], c2[0], c3[0], kernel=(3, 3), stride=(2, 2), padding=(1, 1), groups=keep[40]
        )

        c1 = [(keep[40], keep[41]), (keep[43], keep[44])]
        c2 = [(keep[41], keep[42]), (keep[44], keep[45])]
        c3 = [(keep[42], keep[43]), (keep[45], keep[46])]
        self.conv_5 = Residual(
            c1, c2, c3, num_block=2, groups=keep[40], kernel=(3, 3), stride=(1, 1), padding=(1, 1)
        )
        self.conv_6_sep = ConvBlock(
            keep[46], keep[47], kernel=(1, 1), stride=(1, 1), padding=(0, 0)
        )
        self.conv_6_dw = LinearBlock(
            keep[47], keep[48], groups=keep[48], kernel=conv6_kernel, stride=(1, 1), padding=(0, 0)
        )
        self.conv_6_flatten = nn.Flatten()
        self.linear = nn.Linear(512, embedding_size, bias=False)
        self.bn = nn.BatchNorm1d(embedding_size)
        self.drop = nn.Dropout(p=drop_p)
        self.prob = nn.Linear(embedding_size, num_classes, bias=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = self.conv1(x)
        out = self.conv2_dw(out)
        out = self.conv_23(out)
        out = self.conv_3(out)
        out = self.conv_34(out)
        out = self.conv_4(out)
        out = self.conv_45(out)
        out = self.conv_5(out)
        out = self.conv_6_sep(out)
        out = self.conv_6_dw(out)
        out = self.conv_6_flatten(out)
        if self.embedding_size != 512:
            out = self.linear(out)
        out = self.bn(out)
        out = self.drop(out)
        out = self.prob(out)
        return torch.softmax(out, dim=1)


class MiniFASNetSE(MiniFASNet):
    """MiniFASNet with Squeeze-and-Excitation blocks."""

    def __init__(
        self,
        keep: list[int],
        embedding_size: int = 128,
        conv6_kernel: tuple[int, int] = (5, 5),
        drop_p: float = 0.75,
        num_classes: int = 3,
        img_channel: int = 3,
    ) -> None:
        super().__init__(
            keep=keep,
            embedding_size=embedding_size,
            conv6_kernel=conv6_kernel,
            drop_p=drop_p,
            num_classes=num_classes,
            img_channel=img_channel,
        )

        c1 = [(keep[4], keep[5]), (keep[7], keep[8]), (keep[10], keep[11]), (keep[13], keep[14])]
        c2 = [(keep[5], keep[6]), (keep[8], keep[9]), (keep[11], keep[12]), (keep[14], keep[15])]
        c3 = [(keep[6], keep[7]), (keep[9], keep[10]), (keep[12], keep[13]), (keep[15], keep[16])]
        self.conv_3 = ResidualSE(
            c1, c2, c3, num_block=4, groups=keep[4], kernel=(3, 3), stride=(1, 1), padding=(1, 1)
        )

        c1 = [
            (keep[19], keep[20]),
            (keep[22], keep[23]),
            (keep[25], keep[26]),
            (keep[28], keep[29]),
            (keep[31], keep[32]),
            (keep[34], keep[35]),
        ]
        c2 = [
            (keep[20], keep[21]),
            (keep[23], keep[24]),
            (keep[26], keep[27]),
            (keep[29], keep[30]),
            (keep[32], keep[33]),
            (keep[35], keep[36]),
        ]
        c3 = [
            (keep[21], keep[22]),
            (keep[24], keep[25]),
            (keep[27], keep[28]),
            (keep[30], keep[31]),
            (keep[33], keep[34]),
            (keep[36], keep[37]),
        ]
        self.conv_4 = ResidualSE(
            c1, c2, c3, num_block=6, groups=keep[19], kernel=(3, 3), stride=(1, 1), padding=(1, 1)
        )

        c1 = [(keep[40], keep[41]), (keep[43], keep[44])]
        c2 = [(keep[41], keep[42]), (keep[44], keep[45])]
        c3 = [(keep[42], keep[43]), (keep[45], keep[46])]
        self.conv_5 = ResidualSE(
            c1, c2, c3, num_block=2, groups=keep[40], kernel=(3, 3), stride=(1, 1), padding=(1, 1)
        )


KEEP_DICT = {
    "1.8M": [
        32,
        32,
        103,
        103,
        64,
        13,
        13,
        64,
        26,
        26,
        64,
        13,
        13,
        64,
        52,
        52,
        64,
        231,
        231,
        128,
        154,
        154,
        128,
        52,
        52,
        128,
        26,
        26,
        128,
        52,
        52,
        128,
        26,
        26,
        128,
        26,
        26,
        128,
        308,
        308,
        128,
        26,
        26,
        128,
        26,
        26,
        128,
        512,
        512,
    ],
    "1.8M_": [
        32,
        32,
        103,
        103,
        64,
        13,
        13,
        64,
        13,
        13,
        64,
        13,
        13,
        64,
        13,
        13,
        64,
        231,
        231,
        128,
        231,
        231,
        128,
        52,
        52,
        128,
        26,
        26,
        128,
        77,
        77,
        128,
        26,
        26,
        128,
        26,
        26,
        128,
        308,
        308,
        128,
        26,
        26,
        128,
        26,
        26,
        128,
        512,
        512,
    ],
}


def build_model(arch: str) -> nn.Module:
    """Build the model by architecture name for 80x80 input (kernel=(5,5))."""
    if arch == "MiniFASNetV2":
        return MiniFASNet(
            KEEP_DICT["1.8M_"],
            embedding_size=128,
            conv6_kernel=(5, 5),
            drop_p=0.2,
            num_classes=3,
        )
    if arch == "MiniFASNetV1SE":
        return MiniFASNetSE(
            KEEP_DICT["1.8M"],
            embedding_size=128,
            conv6_kernel=(5, 5),
            drop_p=0.75,
            num_classes=3,
        )
    raise ValueError(f"unknown architecture: {arch}")


# ---------------------------------------------------------------- conversion


def download_pth(filename: str) -> Path:
    """Download the .pth weights to TMP_DIR if not present."""
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    target = TMP_DIR / filename
    if not target.exists():
        url = BASE_URL + filename
        print(f"Downloading {url} ...")
        with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310
            target.write_bytes(resp.read())
    return target


def convert_model(pth_file: str, onnx_file: str, arch: str) -> None:
    """Convert one PyTorch .pth model to ONNX."""
    pth_path = download_pth(pth_file)
    model = build_model(arch)

    state_dict = torch.load(pth_path, map_location="cpu", weights_only=True)
    cleaned_dict: OrderedDict[str, torch.Tensor] = OrderedDict()
    for k, v in state_dict.items():
        name = k[7:] if k.startswith("module.") else k
        cleaned_dict[name] = v

    model.load_state_dict(cleaned_dict, strict=True)
    model.eval()

    dummy_input = torch.randn(1, 3, 80, 80, dtype=torch.float32)
    output_path = MODELS_DIR / onnx_file
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Exporting {onnx_file} ...")
    torch.onnx.export(
        model,
        dummy_input,
        str(output_path),
        input_names=["input"],
        output_names=["output"],
        dynamic_axes={"input": {0: "batch"}, "output": {0: "batch"}},
        opset_version=17,
        do_constant_folding=True,
    )

    # Verify with onnxruntime
    session = ort.InferenceSession(str(output_path), providers=["CPUExecutionProvider"])
    ort_out = session.run(["output"], {"input": dummy_input.numpy()})[0]
    with torch.no_grad():
        pt_out = model(dummy_input).numpy()
    max_diff = float((ort_out - pt_out).max())
    print(f"Validated {onnx_file} with onnxruntime. Max diff: {max_diff:.6e}")
    assert max_diff < 1e-4, f"Output mismatch: {max_diff}"

    sha256 = hashlib.sha256(output_path.read_bytes()).hexdigest()
    print(f"SHA-256 for {onnx_file}: {sha256}\n")


def main() -> None:
    """Convert all anti-spoofing models."""
    for pth_file, onnx_file, arch in MODEL_FILES:
        convert_model(pth_file, onnx_file, arch)
    print("All Silent-Face Anti-Spoofing models converted successfully.")


if __name__ == "__main__":
    main()
