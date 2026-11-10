# MiniFASNet PyTorch to ONNX Conversion Notes

## Upstream Provenance
* **Repository**: [minivision-ai/Silent-Face-Anti-Spoofing](https://github.com/minivision-ai/Silent-Face-Anti-Spoofing)
* **License**: Apache-2.0
* **Upstream Commit**: `b6d5f04ad78778917853b25c778acef6d5626d15` (2020-08-05)

## Source Checkpoints (.pth)
* `resources/anti_spoof_models/2.7_80x80_MiniFASNetV2.pth`
  * Size: 1,849,453 bytes
  * SHA-256: `a5eb02e1843f19b5386b953cc4c9f011c3f985d0ee2bb9819eea9a142099bec0`
* `resources/anti_spoof_models/4_0_0_80x80_MiniFASNetV1SE.pth`
  * Size: 1,856,130 bytes
  * SHA-256: `84ee1d37d96894d5e82de5a57df044ef80a58be2b218b5ed7cdfd875ec2f5990`

## Conversion Environment & Settings
* **PyTorch Version**: `torch==2.6.0`
* **ONNX Package**: `onnx==1.17.0`
* **ONNX Opset Version**: `17`
* **Constant Folding**: `True`
* **Input Specifications**: `name="input"`, `shape=(batch, 3, 80, 80)`, dynamic batch axis `{0: "batch"}`
* **Output Specifications**: `name="output"`, `shape=(batch, 3)` (probabilities, `torch.softmax(out, dim=1)`), dynamic batch axis `{0: "batch"}`
* **Weights Loading**: `torch.load(..., map_location="cpu", weights_only=True)` and `model.load_state_dict(cleaned_dict, strict=True)`

## Output Models (.onnx)
* `ai/models/2.7_80x80_MiniFASNetV2.onnx`
  * SHA-256: `39397b412ac2a3f39dde90cb45fb8525b8cf8018355ec2f1a402b24b01d9c7f8`
* `ai/models/4_0_0_80x80_MiniFASNetV1SE.onnx`
  * SHA-256: `4238cb5bc78f225644ed3f6078a46463884696e2d9fd5efb1cbfc15ef723ce06`

## How to Run
```bash
uv run --with "torch==2.6.0" --with "onnx==1.17.0" python ai/scripts/convert_silent_face_onnx.py
```
