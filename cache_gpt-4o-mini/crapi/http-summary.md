# HTTP 请求和响应捕获摘要
生成时间: 4/29/2026, 4:20:59 PM

## 概述
- 总请求数: 259
- 独立端点数: 70

## 按上下文分类
### initial-load
- 请求数量: 7
- HTTP方法分布:
  - GET: 4次

### login-detection
- 请求数量: 10
- HTTP方法分布:
  - GET: 5次

### login-process
- 请求数量: 31
- HTTP方法分布:
  - GET: 8次
  - POST: 2次

### login-verification
- 请求数量: 23
- HTTP方法分布:
  - GET: 13次

### page-load-http%3A%2F%2Fexample.test%3A8888%2F
- 请求数量: 5
- HTTP方法分布:

### click-BUTTON-按钮-Vehicle Service Hist-1
- 请求数量: 2
- HTTP方法分布:
  - GET: 1次

### click-SPAN-按钮-Vehicle Service Hist-2
- 请求数量: 1
- HTTP方法分布:

### click-BUTTON-按钮-Contact Mechanic-3
- 请求数量: 2
- HTTP方法分布:
  - GET: 1次

### click-SPAN-按钮-Contact Mechanic-4
- 请求数量: 1
- HTTP方法分布:

### click-BUTTON-按钮-Refresh Location-5
- 请求数量: 46
- HTTP方法分布:
  - GET: 14次
  - POST: 1次
  - OPTIONS: 1次

### click-LI-菜单项-Dashboard-7
- 请求数量: 63
- HTTP方法分布:
  - GET: 28次
  - POST: 1次

### click-LI-菜单项-Shop-8
- 请求数量: 9
- HTTP方法分布:
  - GET: 1次

### click-LI-菜单项-Community-9
- 请求数量: 18
- HTTP方法分布:
  - GET: 1次

### submit-form-1
- 请求数量: 3
- HTTP方法分布:
  - GET: 1次

### page-load-http%3A%2F%2Fexample.test%3A8888%2Fvehicle-service-dashboard%3FVIN%3D896480MP7DZHB9D69
- 请求数量: 4
- HTTP方法分布:
  - GET: 1次

### click-LI-菜单项-Dashboard-1
- 请求数量: 14
- HTTP方法分布:

### click-LI-菜单项-Shop-2
- 请求数量: 10
- HTTP方法分布:

### click-LI-菜单项-Community-3
- 请求数量: 10
- HTTP方法分布:

## 主要API端点
### 1. GET /maps/vt (调用12次)
- 调用上下文:
  - click-BUTTON-按钮-Refresh Location-5: 11次
  - click-LI-菜单项-Dashboard-7: 1次
- 查询参数:
  - pb
  - key
  - token
- 常见请求头:

### 2. POST /$rpc/google.internal.maps.mapsjs.v1.MapsJsInternalService/GetViewportInfo (调用2次)
- 调用上下文:
  - click-BUTTON-按钮-Refresh Location-5: 1次
  - click-LI-菜单项-Dashboard-7: 1次
- 常见请求头:
  - content-type: application/json+protobuf

### 3. GET /css (调用2次)
- 调用上下文:
  - click-LI-菜单项-Dashboard-7: 2次
- 查询参数:
  - family
  - text
  - lang
- 常见请求头:

### 4. GET / (调用1次)
- 调用上下文:
  - initial-load: 1次
- 常见请求头:

### 5. GET /static/js/main.8c78208c.js (调用1次)
- 调用上下文:
  - initial-load: 1次
- 常见请求头:

### 6. GET /static/css/main.dd33429d.css (调用1次)
- 调用上下文:
  - initial-load: 1次
- 常见请求头:

### 7. GET /manifest.json (调用1次)
- 调用上下文:
  - initial-load: 1次
- 常见请求头:

### 8. GET /images/favicon.ico (调用1次)
- 调用上下文:
  - login-detection: 1次
- 常见请求头:

### 9. GET image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Cdefs%3E%3Cpattern id='a' width='10' height='10' patternUnits='userSpaceOnUse'%3E%3Cpath d='M10 0H0v10' fill='none' stroke='rgba(255,255,255,0.1)'/%3E%3C/pattern%3E%3C/defs%3E%3Cpath fill='url(%23a)' d='M0 0h100v100H0z'/%3E%3C/svg%3E (调用1次)
- 调用上下文:
  - login-detection: 1次
- 常见请求头:

### 10. GET image/svg+xml,%3csvg%20xmlns='http://www.w3.org/2000/svg'%20xml:space='preserve'%20viewBox='0%200%201000%201000'%3e%3crect%20width='100%25'%20height='100%25'%20fill='%23fff'/%3e%3cg%3e%3crect%20width='45'%20height='30'%20x='-22.5'%20y='-15'%20rx='1.5'%20ry='1.5'%20style='stroke:none;stroke-width:1;stroke-dasharray:none;stroke-linecap:butt;stroke-dashoffset:0;stroke-linejoin:miter;stroke-miterlimit:4;is-custom-font:none;font-file-url:none;fill:%2337547a;fill-rule:nonzero;opacity:1'%20transform='rotate(90%20-99%20362)%20scale(3.4003)'/%3e%3cpath%20d='M0%2075q14%200%2014%2017%200%2016-14%2016Z'%20style='stroke:none;stroke-width:1;stroke-dasharray:none;stroke-linecap:butt;stroke-dashoffset:0;stroke-linejoin:miter;stroke-miterlimit:4;is-custom-font:none;font-file-url:none;fill:%2398b0ca;fill-rule:nonzero;opacity:1'%20transform='rotate(180%20106%20386)%20scale(3.4003)'/%3e%3crect%20width='25'%20height='4'%20x='-12.5'%20y='-2'%20rx='.2'%20ry='.2'%20style='stroke:%2346648c;stroke-width:1;stroke-dasharray:none;stroke-linecap:butt;stroke-dashoffset:0;stroke-linejoin:miter;stroke-miterlimit:4;is-custom-font:none;font-file-url:none;fill:%2346648c;fill-rule:nonzero;opacity:1'%20transform='rotate(90%20-46%20282)%20scale(3.4003)'/%3e%3cpath%20d='M19%2065h4l5%204v1H14v-1Z'%20style='stroke:none;stroke-width:1;stroke-dasharray:none;stroke-linecap:butt;stroke-dashoffset:0;stroke-linejoin:miter;stroke-miterlimit:4;is-custom-font:none;font-file-url:none;fill:%2346648c;fill-rule:nonzero;opacity:1'%20transform='translate(165%20150)%20scale(3.4003)'/%3e%3crect%20width='25'%20height='2'%20x='-12.5'%20y='-1'%20rx='.1'%20ry='.1'%20style='stroke:%2398b0ca;stroke-width:1;stroke-dasharray:none;stroke-linecap:butt;stroke-dashoffset:0;stroke-linejoin:miter;stroke-miterlimit:4;is-custom-font:none;font-file-url:none;fill:%2398b0ca;fill-rule:nonzero;opacity:1'%20transform='rotate(90%20-3%20240)%20scale(3.4003)'/%3e%3ccircle%20r='7.5'%20style='stroke:none;stroke-width:1;stroke-dasharray:none;stroke-linecap:butt;stroke-dashoffset:0;stroke-linejoin:miter;stroke-miterlimit:4;is-custom-font:none;font-file-url:none;fill:%23a478fc;fill-rule:nonzero;opacity:1'%20transform='translate(236%20175)%20scale(3.4003)'/%3e%3crect%20width='45'%20height='30'%20x='-22.5'%20y='-15'%20rx='1.5'%20ry='1.5'%20style='stroke:none;stroke-width:1;stroke-dasharray:none;stroke-linecap:butt;stroke-dashoffset:0;stroke-linejoin:miter;stroke-miterlimit:4;is-custom-font:none;font-file-url:none;fill:%2337547a;fill-rule:nonzero;opacity:1'%20transform='rotate(90%20139%20600)%20scale(3.4003)'/%3e%3cpath%20d='M184%2075q14%200%2014%2017%200%2016-14%2016Z'%20style='stroke:none;stroke-width:1;stroke-dasharray:none;stroke-linecap:butt;stroke-dashoffset:0;stroke-linejoin:miter;stroke-miterlimit:4;is-custom-font:none;font-file-url:none;fill:%2398b0ca;fill-rule:nonzero;opacity:1'%20transform='translate(165%20150)%20scale(3.4003)'/%3e%3crect%20width='25'%20height='4'%20x='-12.5'%20y='-2'%20rx='.2'%20ry='.2'%20style='stroke:%2346648c;stroke-width:1;stroke-dasharray:none;stroke-linecap:butt;stroke-dashoffset:0;stroke-linejoin:miter;stroke-miterlimit:4;is-custom-font:none;font-file-url:none;fill:%2346648c;fill-rule:nonzero;opacity:1'%20transform='rotate(90%20219%20548)%20scale(3.4003)'/%3e%3cpath%20d='M175%2065h4l5%204v1h-14v-1Z'%20style='stroke:none;stroke-width:1;stroke-dasharray:none;stroke-linecap:butt;stroke-dashoffset:0;stroke-linejoin:miter;stroke-miterlimit:4;is-custom-font:none;font-file-url:none;fill:%2346648c;fill-rule:nonzero;opacity:1'%20transform='translate(165%20150)%20scale(3.4003)'/%3e%3crect%20width='25'%20height='2'%20x='-12.5'%20y='-1'%20rx='.1'%20ry='.1'%20style='stroke:%2398b0ca;stroke-width:1;stroke-dasharray:none;stroke-linecap:butt;stroke-dashoffset:0;stroke-linejoin:miter;stroke-miterlimit:4;is-custom-font:none;font-file-url:none;fill:%2398b0ca;fill-rule:nonzero;opacity:1'%20transform='rotate(90%20262%20505)%20scale(3.4003)'/%3e%3ccircle%20r='7.5'%20style='stroke:none;stroke-width:1;stroke-dasharray:none;stroke-linecap:butt;stroke-dashoffset:0;stroke-linejoin:miter;stroke-miterlimit:4;is-custom-font:none;font-file-url:none;fill:%23a478fc;fill-rule:nonzero;opacity:1'%20transform='translate(767%20175)%20scale(3.4003)'/%3e%3crect%20width='116.7'%20height='70'%20x='-58.4'%20y='-35'%20rx='31.5'%20ry='31.5'%20style='stroke:none;stroke-width:1;stroke-dasharray:none;stroke-linecap:butt;stroke-dashoffset:0;stroke-linejoin:miter;stroke-miterlimit:4;is-custom-font:none;font-file-url:none;fill:%2371c9fc;fill-rule:nonzero;opacity:1'%20transform='translate(502%20877)%20scale(3.4003)'/%3e%3crect%20width='60'%20height='20'%20x='-30'%20y='-10'%20rx='3'%20ry='3'%20style='stroke:none;stroke-width:1;stroke-dasharray:none;stroke-linecap:butt;stroke-dashoffset:0;stroke-linejoin:miter;stroke-miterlimit:4;is-custom-font:none;font-file-url:none;fill:%2337547a;fill-rule:nonzero;opacity:1'%20transform='translate(502%20728)%20scale(3.4003)'/%3e%3ccircle%20r='75'%20style='stroke:none;stroke-width:1;stroke-dasharray:none;stroke-linecap:butt;stroke-dashoffset:0;stroke-linejoin:miter;stroke-miterlimit:4;is-custom-font:none;font-file-url:none;fill:%2371c9fc;fill-rule:nonzero;opacity:1'%20transform='translate(502%20473)%20scale(3.4003)'/%3e%3crect%20width='129.4'%20height='82.3'%20x='-64.7'%20y='-41.1'%20rx='37'%20ry='37'%20style='stroke:none;stroke-width:1;stroke-dasharray:none;stroke-linecap:butt;stroke-dashoffset:0;stroke-linejoin:miter;stroke-miterlimit:4;is-custom-font:none;font-file-url:none;fill:%23fff;fill-rule:nonzero;opacity:1'%20transform='translate(502%20464)%20scale(3.4003)'/%3e%3crect%20width='108'%20height='68.6'%20x='-54'%20y='-34.3'%20rx='30.9'%20ry='30.9'%20style='stroke:none;stroke-width:1;stroke-dasharray:none;stroke-linecap:butt;stroke-dashoffset:0;stroke-linejoin:miter;stroke-miterlimit:4;is-custom-font:none;font-file-url:none;fill:%23162334;fill-rule:nonzero;opacity:1'%20transform='translate(502%20464)%20scale(3.4003)'/%3e%3ccircle%20r='19.4'%20style='stroke:none;stroke-width:1;stroke-dasharray:none;stroke-linecap:butt;stroke-dashoffset:0;stroke-linejoin:miter;stroke-miterlimit:4;is-custom-font:none;font-file-url:none;fill:%23a478fc;fill-rule:nonzero;opacity:1'%20transform='translate(415%20464)%20scale(3.4003)'/%3e%3ccircle%20r='19.4'%20style='stroke:none;stroke-width:1;stroke-dasharray:none;stroke-linecap:butt;stroke-dashoffset:0;stroke-linejoin:miter;stroke-miterlimit:4;is-custom-font:none;font-file-url:none;fill:%23a478fc;fill-rule:nonzero;opacity:1'%20transform='translate(591%20464)%20scale(3.4003)'/%3e%3crect%20width='96'%20height='53.2'%20x='-48'%20y='-26.6'%20rx='23.9'%20ry='23.9'%20style='stroke:%23162334;stroke-width:1;stroke-dasharray:none;stroke-linecap:butt;stroke-dashoffset:0;stroke-linejoin:miter;stroke-miterlimit:4;is-custom-font:none;font-file-url:none;fill:%23162334;fill-rule:nonzero;opacity:1'%20transform='translate(503%20875)%20scale(3.4003)'/%3e%3cpath%20stroke-linecap='round'%20d='m68%20215%207-15M81%20222l-7-22M81%20222l7-14M101%20208H87M114%20219l-7-24M113%20219l13-22M131%20214l-5-17M148%20214h-18M69%20214H51M100%20209l7-15'%20style='stroke:%23a478fc;stroke-width:3;stroke-dasharray:none;stroke-linecap:butt;stroke-dashoffset:0;stroke-linejoin:miter;stroke-miterlimit:10;is-custom-font:none;font-file-url:none;fill:none;fill-rule:nonzero;opacity:1'%20transform='translate(165%20150)%20scale(3.4003)'/%3e%3c/g%3e%3c/svg%3e (调用1次)
- 调用上下文:
  - login-detection: 1次
- 常见请求头:

## 如何使用此数据
- 详细JSON数据位于 `http-requests.json` 和 `http-requests` 文件夹中
- 请求模式分析位于 `http-analysis.json` 中
- 可根据上下文筛选请求，每个上下文的请求都保存在单独的JSON文件中
