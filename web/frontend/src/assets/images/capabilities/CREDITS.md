# 核心能力卡片背景图：来源与授权

本目录下的 4 张图片全部取自 **NASA 公共领域（Public Domain）** 素材。依据 NASA Media Usage
Guidelines，NASA 素材一般不受版权保护，可自由使用；这 4 张图均不含第三方 Logo、水印、
摄影师署名或新闻机构标识。

| 文件 | 用途 | 来源 | 原始素材（NASA Image and Video Library） | 授权 |
| --- | --- | --- | --- | --- |
| `cap-01-data-sources.jpg` | 01 数据获取 | NASA GSFC / MODIS（Aqua、Terra） | “Satellite Eyes First Major Atlantic Hurricane in 3 Years: Gonzalo” — <https://images.nasa.gov/details/GSFC_20171208_Archive_e000947> | Public Domain |
| `cap-02-parameter-inversion.jpg` | 02 参数反演 | NASA / ISS Expedition 23 | “Earth Observations taken by the Expedition 23 Crew”（大气层边缘，ISS023-E-057948） — <https://images.nasa.gov/details/iss023e057948> | Public Domain |
| `cap-03-products.jpg` | 03 产品服务 | NASA Earth Observatory | “Cloudy Earth”（全球云量产品） — <https://images.nasa.gov/details/cloudy-earth_16810027983_o> | Public Domain |
| `cap-04-analysis.jpg` | 04 结果分析 | NASA JPL | “COWVR and TEMPEST Image Hurricane Ian”（微波反演海面风场产品，PIA25425） — <https://images.nasa.gov/details/PIA25425> | Public Domain |

## 处理说明

- 只做等比裁切（cover）与缩放：统一输出 1600 × 667（2.4:1）、JPEG 质量 82。
- 未调色、未加滤镜、未合成、未覆盖任何水印或文字。
- 卡片上的深蓝透明蒙版在 CSS 中实现（`.cap-veil`），不写进图片本身。

## 重新获取

```
https://images-assets.nasa.gov/image/<nasa_id>/<nasa_id>~large.jpg   # 大图
https://images-assets.nasa.gov/image/<nasa_id>/<nasa_id>~orig.jpg    # 原图
```