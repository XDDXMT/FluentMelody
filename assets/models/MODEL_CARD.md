# 本地旋律识别模型

## 来源与用途

本程序使用 Simonetta、Cancino-Chacón、Ntalampiras、Widmer 在 ISMIR 2019 发表的 **A Convolutional Approach to Melody Line Identification in Symbolic Scores** 的作者公开训练权重。它识别符号乐谱中哪些音符更可能属于主旋律，不生成音乐、不识别音频、不调用网络。

- 作者仓库：https://github.com/LIMUNIMI/Symbolic-Melody-Identification
- 论文：https://github.com/LIMUNIMI/Symbolic-Melody-Identification/blob/master/paper.pdf
- 原始权重：https://github.com/LIMUNIMI/Symbolic-Melody-Identification/blob/master/nn_kernels_pop.pkl
- 许可：https://github.com/LIMUNIMI/Symbolic-Melody-Identification/blob/master/LICENSE
- 训练数据说明：https://github.com/LIMUNIMI/Symbolic-Melody-Identification/blob/master/data/README.md

作者的 POP 数据集包含 83 首流行及爵士曲目，以人声声部作为旋律标注。原始 POP 曲谱已因版权原因从作者数据目录移除；本程序只分发作者仍公开发布的模型权重，不包含该训练曲谱。它不是 POP909 模型，也没有针对用户歌曲重新训练。模型和来源仓库采用 MIT 许可，完整许可随程序放在 `licenses/Symbolic-Melody-Identification-MIT.txt`。

## 参数与文件

- 模型标识：`simonetta2019-pop-numpy-v1`。
- 原始 pickle：2,601,524 字节，SHA-256 `36bfab4ee18c7090c0c95fc308b025ab02473095e5329031d71d86382cba2a2d`。
- 参数：两个 float32 数组，尺寸 `(21, 1, 32, 16)` 与 `(21, 21, 32, 16)`，共 236,544 个参数，原始数组 946,176 字节。
- 安全 NPZ：`symbolic_melody_pop.npz`，874,991 字节，SHA-256 `82b2ec1984c5dfb91248c37caff503993dc188665d7bab0a8ec0e27f55664604`。
- 转换仅改变存储格式，未改变参数数值。开发时用只允许 NumPy 数组/数据类型重建的受限解包器读取原文件；运行时只使用 `allow_pickle=False` 的 NPZ，并核对 SHA-256。

## 推理方式

原实现使用 Python 2、Theano 和 Lasagne。本程序重写 NumPy 推理，不携带这些框架，也不依赖 PyTorch、TensorFlow 或 ONNX Runtime。FFT 卷积缓存约 31.6 MB 内存，不计入发布文件体积。

输入为 128 个 MIDI 音高的二值钢琴卷帘图，每拍 8 个采样格、每个窗口 64 格、窗口重叠一半。两个卷积层后接共享权重的两个反向层和 sigmoid；反向层保留原 Lasagne InverseLayer 中的 sigmoid 导数。输出与输入音符掩码相乘，再将每个音符覆盖位置的概率取中位数。

`predict_melody` 接收含 `start`、`duration`、`midi` 的音符对象，返回同顺序的 0～1 旋律显著性。如果有 `gate_duration`，优先采用实际松键时长，以免延音踏板残响把谱面填成持续和弦；没有该字段时使用 `duration`。默认每拍 0.5 秒，调用方可以提供歌曲节拍；时间以秒表示的 MIDI/NBS 都能接入。最大 50,000 音符、1,800 秒和 120,000 采样格。首尾使用补零窗口，避免短曲和最后一个音符漏算。

## 验证与局限

为验证移植数学正确性，使用作者公开的 Mozart 权重和 Gluck《Die Sommernacht》参考概率矩阵逐像素核对：最大误差约 `1.28e-5`、平均绝对误差约 `8.9e-9`。完整 128×960 输入在开发电脑 NumPy 2.2.6 上约 0.53 秒；这不是所有电脑的速度保证。回归测试保留该公开参考中的短片段与对应 Mozart 权重，文件只在源码测试目录，不进入客户端资源。

本模型来自较小的曲谱数据集，无法保证对每一首歌曲优于规则选择。NBS 简化时值、强烈变速、分轨方式、伴奏高音和稠密和弦都会影响结果。输出分数用于辅助主旋律选择，不能理解为经过校准的正确概率。声部连续性、前奏/间奏选择、音域及 0.1 秒松键间隔由后续编排逻辑处理；模型本身不会改变或播放歌曲。

## 在 Fluent Melody 中的使用

默认自动转换将模型分数与音轨特征、乐句休止、重复同音及节拍取舍共同使用。对缺乏人声特征的器乐段，结合局部音区和连续性保留前奏、间奏与尾奏。为了满足单音与至少 0.1 秒的松键间隔，后续编排可进行最多 50 毫秒的局部起音调整；不累积延迟，仍过密时会减速或删减。

已编排且声部数匹配的 NBS 优先保留原编排。重新识别应使用原始 MIDI / NBS。模型异常时会明确提示并回退到音轨与乐句规则；此本地识别流程无需 Beta 模式、服务器或 token，与服务器 AI 编曲分开。

已有针对用户具体歌曲制作的演奏版只用于回归对比，不是人工标注标准谱，不据此宣称识别准确率。用户使用说明见程序根目录的 `本地旋律识别说明.md`。
