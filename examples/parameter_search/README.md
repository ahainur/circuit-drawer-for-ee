# 参数范围演示

1、打开 `buck.json`，在“仿真 → SPICE 仿真…”展开“参数范围搜索…”。此例从零初值运行 50 ms；原有 `examples/spice/buck.json` 保留不变。

2、目标为 `v_out_V` 的 12 V，扫描 L1 = 400／500／600 µH、S1 = 40／50／60 kHz，共九组。输出误差上限 5%、纹波上限 1%、启动过冲上限 75%。过冲限值用于演示筛选，不是设计标准；不满足周期重复性的结果仍不能采用。

3、不填写损耗文件时可以比较纹波和过冲。已安装包含磁件与快照接口的 powerdesignkit 后，填写 `power-data.json`，比较估算效率和磁件包络体积。每组的器件、温度、磁件尺寸、材料系数、ESR 及辅助损耗都是**合成演示数据**，没有对应厂商型号，不是制作建议。

4、命令行在仓库根目录执行：

```console
circuit-drawer parameter-search examples/parameter_search/buck.json --config examples/parameter_search/buck-search.json --out out/search
```

5、每次产生独立结果文件夹，保留参数、原图、仿真 CSV、原始积分点压缩包、损耗明细和数据快照。命令不会写回图纸。只需电压指标时，从扫描配置删除 `power_file`。格式与假设见 [参数搜索说明](../../docs/PARAMETER_SEARCH.md)。
