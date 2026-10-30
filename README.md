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

## 运行环境与启动

需要 Python 3.10+、`pyproject.toml` 中声明的依赖，以及用于启动 ASGI 服务的 Uvicorn；运行测试还需要 pytest、httpx。以下命令在项目根目录执行，Python 路径不绑定某台机器。可在自己选定的虚拟环境中运行，不需要后台常驻脚本：

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m uvicorn --app-dir src service.app:app --host 127.0.0.1 --port 8002
```

打开 `http://127.0.0.1:8002/`。不要直接双击 HTML；页面的公式请求和本地 Leaflet 资源由同一服务提供。使用 OSM 或 Carto 底图配合 EPSG:3857 的 XYZ 结果，未加入坐标偏移转换的底图不参与叠加。底图依赖外部地图网络；不影响本地 API 计算。

## 可复现的栅格演示

另开终端或停止手动运行的服务后，在项目根目录生成自建双波段 GeoTIFF：

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
python -m service.demo --output './data/demo_bands.tif' --size 256
$env:TILE_RASTER_PATH = (Resolve-Path './data/demo_bands.tif').Path
python -m uvicorn --app-dir src service.app:app --host 127.0.0.1 --port 8002
```

生成器固定使用 EPSG:4326、经度 114.2–114.6°、纬度 30.3–30.7° 范围，输出 `B4`、`B8` 两个确定性渐变波段和左上无数据区。它是功能验证用的自建数据，不代表武汉的实际卫星观测。`--size` 允许 32–2048；`--output` 可指定其他路径。**目标已存在时直接失败，不覆盖已有数据**；重复演示可复用原文件，或选择新的输出名。生成文件由 `.gitignore` 排除。

浏览器操作：

1. 访问服务首页，选择“服务端配置栅格”；页面读取文件元数据，显示尺寸、CRS 和 `B4/B8` 波段，并自动定位实际覆盖范围。
2. 使用默认 NDVI 表达式，点击“计算地图中心所在瓦片”。地图叠加结果、预览和统计均来自后端实际栅格读取；左上无数据或范围外像元透明。
3. 选择 NDWI/EVI 时会提示缺少 `B3/B2` 波段；两波段演示不伪造这些波段。使用自有多波段文件时，按文件实际描述选择公式。
4. 修改色带可重新显示当前结果；切换数据来源会清理旧统计和图像。栅格配置缺失、缺 CRS 或文件损坏时显示读取错误，不退回模拟结果。

可在另一个终端检查同一演示链：

```powershell
Invoke-RestMethod 'http://127.0.0.1:8002/health'
Invoke-RestMethod 'http://127.0.0.1:8002/sources/raster'
Invoke-RestMethod 'http://127.0.0.1:8002/tiles/10/837/420/calc?source=raster'
Invoke-WebRequest 'http://127.0.0.1:8002/tiles/10/837/420/image.png?source=raster' -OutFile './data/demo_ndvi.png'
```

示例 XYZ 位于生成器覆盖范围内，统计应存在有效像元且 NDVI 在 0–1 之间。自有影像必须使用其覆盖范围内的 XYZ，不能复用这组坐标。启用自有栅格时，在启动命令前设置服务端文件路径，再在页面选择对应来源：

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
- `GET /health`：服务存活状态；不依赖栅格是否正确配置。
- `GET /sources/raster`：验证栅格可读性，返回文件名、尺寸、原 CRS、EPSG:4326 覆盖范围、可用波段名称／物理序号／类型，以及是否为自建演示数据。不会返回服务端绝对路径；缺配置、缺 CRS、无法转换的范围和读取失败返回 400。跨日期变更线或完全处于 Web Mercator 纬度范围外的数据暂不支持地图定位。
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
node --test tests/test_viewer.js
```

Python 测试覆盖表达式数值与拦截、掩码／nodata／除零、不裁剪通用结果、XYZ 合法性与共享边界、局部随机源可复现、输入数组及统计接口、自建双波段 GeoTIFF 的 EPSG:4326→3857 读取与不同 XYZ 范围、覆盖区外无数据、PNG 校验和与透明度，以及页面与本地资源路由。新增演示链测试从文件生成、元数据、实际 NDVI 计算走到 PNG 输出，同时检查拒绝覆盖、确定性输出、元数据重投影和错误路径脱敏。

前端测试使用 Node.js 18+ 自带 `node:test`，不需要 npm 依赖；覆盖真实范围定位、波段展示、缺波段提示、读取失败与来源切换竞态。测试不访问外部地图或模型，不启动服务。
