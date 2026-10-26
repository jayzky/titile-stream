# TiTile-Stream

一个可验证的波段表达式与 XYZ 瓦片计算原型。后端提供 NumPy 数组计算、Rasterio 栅格范围读取与重投影、统计 JSON 和透明 PNG；前端提交实际公式并显示后端结果，没有随机统计或前端伪造像元。

## 数据来源与实现边界

- **模拟模式**：使用每个 XYZ 坐标独立的局部随机数生成器创建 256×256 波段，重复请求可复现；左上 8×8 像元标记为无数据。模拟波段与底图卫星图无关。
- **栅格模式**：仅读取服务端环境变量 `TILE_RASTER_PATH` 配置的本地栅格，不接收客户端文件路径。文件须具有 CRS；按 XYZ 范围通过 WarpedVRT 以最近邻重投影到 EPSG:3857 的 256×256 网格，源 nodata 与覆盖范围外像元保留为无数据。
- 波段名称优先使用文件中的波段描述，例如双波段文件可将第 1、2 波段描述设为 `B4`、`B8`；没有描述时使用物理顺序 `B1`、`B2` 等。允许名称为 `B1/B2/B3/B4/B8/B11/B12`。缺少表达式所需波段时明确报错。
- PNG 使用每张结果图的有效值范围作颜色拉伸，无数据像元透明；颜色拉伸不改变统计数值，不能用不同瓦片的相同颜色直接比较数值。
- 自建测试 GeoTIFF 只用于验证读取与计算，不是实测卫星影像。本项目未验证远程 COG、并发吞吐、缓存、生产部署或真实遥感反演精度。

## 表达式与无数据处理

允许有限数值、波段变量、括号、正负号及 `+ - * /`，不执行 Python `eval`。限制表达式长度、AST 节点数量及嵌套深度，禁止函数调用、属性访问、索引及幂运算。

参与运算的波段必须是同形状非空二维数组。输入掩码、显式 nodata、NaN、无穷及除零产生的非有限结果均为无数据，返回 JSON 时转换为 `null`，统计只使用有效像元。全无数据时统计值为 `null`。通用表达式结果不裁剪到 `[-1,1]`。

示例：

- NDVI：`(B8 - B4) / (B8 + B4 + 1e-6)`
- NDWI：`(B3 - B8) / (B3 + B8 + 1e-6)`
- EVI：`2.5 * (B8 - B4) / (B8 + 6 * B4 - 7.5 * B2 + 1)`

## 本机运行

在本目录使用已具备项目依赖和 Uvicorn 的 Python 环境手动执行，不需要后台常驻脚本：

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m uvicorn --app-dir src service.app:app --host 127.0.0.1 --port 8002
```

打开 `http://127.0.0.1:8002/`。不要直接双击 HTML；页面的公式请求和本地 Leaflet 资源由同一服务提供。使用 OSM 或 Carto 底图配合 EPSG:3857 的 XYZ 结果，未加入坐标偏移转换的底图不参与叠加。底图依赖外部地图网络；不影响本地 API 计算。

启用自有栅格时，在启动命令前设置服务端文件路径，然后在页面选择“服务端配置栅格”，把地图移到文件覆盖范围内计算：

```powershell
$env:TILE_RASTER_PATH = 'D:/data/my_multiband.tif'
```

如果本机报 PROJ 数据库版本冲突，先检查环境是否指向其他 PostgreSQL/GDAL 的旧数据库。针对当前 Rasterio 环境，可只在本次 PowerShell 进程指定其自带数据库后再启动或测试，不修改系统环境：

```powershell
$env:PROJ_DATA = (python -c "from pathlib import Path; import rasterio; print(Path(rasterio.__file__).parent / 'proj_data')").Trim()
$env:PROJ_LIB = $env:PROJ_DATA
```

仅在上述目录确有 `proj.db` 时使用此配置；不同发行环境应使用自身兼容的 PROJ 数据。

## API

- `GET /sources`：是否配置了栅格文件；配置存在不代表文件已经成功打开。
- `GET /tiles/{z}/{x}/{y}/bounds`：XYZ 边界，经纬度单位为度，采用 EPSG:4326；XYZ 的 y 从北向南递增。
- `GET /tiles/{z}/{x}/{y}/calc?expr=...&source=simulated`：返回形状、有效／无数据计数及均值、极值、标准差。`source=raster` 读取配置文件，`include_pixels=true` 另返回实际结果数组。
- `GET /tiles/{z}/{x}/{y}/image.png?expr=...&source=simulated&colormap=ndvi`：输出 RGBA PNG，可选 `ndvi/water/turbo` 色带。
- `POST /calculate`：计算客户端提交的小型实际数值数组，单波段不超过 512×512，支持 `null` 和显式 nodata。例如请求体：

```json
{"expression":"B8-B4","bands":{"B4":[[1,-9999]],"B8":[[5,3]]},"nodata":-9999}
```

该例返回像元 `[[4,null]]`，均值为 4，有效与无数据像元各 1。没有指定来源的 GET 请求默认明确使用模拟模式；栅格模式读取失败不会退回模拟结果。

坐标限制为整数 `0≤z≤22`、`0≤x,y<2^z`。非法公式、缺波段、形状不一致、非法瓦片及读取失败返回错误；服务没有鉴权，不应直接暴露到公网。

## 验证

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m pytest -p no:cacheprovider -q
```

测试覆盖表达式数值与拦截、掩码／nodata／除零、不裁剪通用结果、XYZ 合法性与共享边界、局部随机源可复现、输入数组及统计接口、自建双波段 GeoTIFF 的 EPSG:4326→3857 读取与不同 XYZ 范围、覆盖区外无数据、PNG 校验和与透明度，以及页面与本地资源路由。测试不访问外部地图或模型，不启动服务。
