# Third-Party Notices

FluentPy may use or adapt resources from third-party open-source projects only when their license is compatible with this project.

## Fluent Melody local melody recognition

- Model: A Convolutional Approach to Melody Line Identification in Symbolic Scores, ISMIR 2019, by Federico Simonetta, Carlos Eduardo Cancino-Chacón, Stavros Ntalampiras, and Gerhard Widmer.
- Source: https://github.com/LIMUNIMI/Symbolic-Melody-Identification
- License: MIT; see `Symbolic-Melody-Identification-MIT.txt`.
- Usage: unchanged published POP pretrained kernels stored as safe NPZ, with NumPy inference. See the bundled `assets/models/MODEL_CARD.md`.
- NumPy 2.2.6 and its bundled numerical libraries: see `dependencies/numpy/LICENSE.txt` for applicable BSD and third-party notices. No PyTorch, Theano, Lasagne, or ONNX Runtime is bundled.

## Qt / PySide6

Qt and PySide6 are third-party windowing and drawing dependencies, not authored by FluentPy. Their license documents are in `Qt/` and `dependencies/`; FluentPy's MIT license does not replace them.

## Microsoft Fluent UI System Icons

- Source: https://github.com/microsoft/fluentui-system-icons
- Copyright (c) 2020 Microsoft Corporation.
- License: MIT; complete text in `Microsoft-Fluent-System-Icons-LICENSE.txt`.
- Usage: bundled SVG icons for `IconButton`, examples, and future controls.
- Bundled files: `fluentpy/assets/icons/ic_fluent_*_24_regular.svg`.

## Microsoft WinUI Gallery

- Source: https://github.com/microsoft/WinUI-Gallery
- License: MIT
- Usage: reference for component gallery organization and Fluent control presentation patterns.

## Microsoft WinUI

- Source: https://github.com/microsoft/microsoft-ui-xaml
- License: MIT
- Usage: reference for WinUI control behavior, theme resources, and state model where individual files are covered by the repository license. Current reference: `controls/dev/CommonStyles/Button_themeresources.xaml`.
