class RequestCapture {
    constructor(page) {
        this.page = page;
        this.requests = [];
        this.context = null;
        this.outputPath = './results_v1'; // 默认输出路径
        this.autoSaveTimer = null;
        this.autoSaveInterval = 30000; // 30秒自动保存一次
        this._onRequest = this._onRequest.bind(this);
        this._onResponse = this._onResponse.bind(this);
        this._setupAutoSave();
        // 新增：记录本次捕获会话开始时的请求计数，用于判断是否有新请求
        this.sessionStartIndex = 0;
        // 新增：记录最近一次自动保存的请求计数，避免重复保存
        this.lastSavedIndex = 0;
        // 新增：请求去重集合
        this.requestSignatures = new Set();
    }
    
    _setupAutoSave() {
        // 设置自动定期保存
        if (this.autoSaveTimer) {
            clearInterval(this.autoSaveTimer);
        }
        this.autoSaveTimer = setInterval(() => {
            if (this.requests.length > 0) {
                console.log(`[RequestCapture] 自动定期保存 ${this.requests.length} 个请求...`);
                this._autoSaveToTempFile();
            }
        }, this.autoSaveInterval);
        if (typeof this.autoSaveTimer.unref === 'function') {
            this.autoSaveTimer.unref();
        }
    }

    async startCapture(context, outputPath) {
        this.context = context;
        if (outputPath) {
            this.outputPath = outputPath;
        }
        // 记录当前会话开始时的请求计数
        this.sessionStartIndex = this.requests.length;
        // 不每次都清空请求数组，而是累加
        // this.requests = [];
        this.page.on('request', this._onRequest);
        this.page.on('response', this._onResponse);
        console.log(`[RequestCapture] 开始捕获请求 (上下文: ${context})`);
    }
    
    // 添加自动保存到临时文件的私有方法
    _autoSaveToTempFile() {
        try {
            const fs = require('fs');
            const path = require('path');
            
            const outputPath = this.outputPath || './results_v1'; // 确保始终有输出路径
            
            if (!fs.existsSync(outputPath)) {
                fs.mkdirSync(outputPath, { recursive: true });
            }
            
            // 仅在有新增请求时进行自动保存，避免重复IO
            if (this.requests.length <= this.lastSavedIndex) {
                console.log(`[RequestCapture] 自动保存跳过：无新增请求（总计 ${this.requests.length}，上次保存到 ${this.lastSavedIndex}）`);
                return;
            }
            
            // 保存到临时文件
            const tempFile = path.join(outputPath, 'http-requests.json');
            
            // 按上下文分组请求
            const requestsByContext = {};
            this.requests.forEach(req => {
                const context = req.context || 'unknown';
                if (!requestsByContext[context]) {
                    requestsByContext[context] = [];
                }
                requestsByContext[context].push(req);
            });
            
            // 保存请求数据
            fs.writeFileSync(tempFile, JSON.stringify({
                total: this.requests.length,
                byContext: Object.keys(requestsByContext).map(context => ({
                    context,
                    count: requestsByContext[context].length
                })),
                requestsAnalysis: {
                    uniqueEndpoints: 0,
                    totalRequests: this.requests.filter(r => r.type === 'request').length
                },
                allRequests: this.requests
            }, null, 2));
            
            // 更新最近一次保存计数
            this.lastSavedIndex = this.requests.length;
            
            console.log(`[RequestCapture] 自动保存: 已将当前 ${this.requests.length} 个请求保存到临时文件 ${tempFile}`);
        } catch (err) {
            console.error(`[RequestCapture] 自动保存失败:`, err.message);
        }
    }
    
    async saveCapturedRequestsToFile(outputPath) {
        // 增加中间保存功能，在每次请求捕获后自动保存到临时文件
        const fs = require('fs');
        const path = require('path');
        
        if (!fs.existsSync(outputPath)) {
            fs.mkdirSync(outputPath, { recursive: true });
        }
        
        // 保存当前请求数据到临时文件
        const tempFile = path.join(outputPath, 'http-requests-temp.json');
        fs.writeFileSync(tempFile, JSON.stringify({
            timestamp: new Date().toISOString(),
            totalSoFar: this.requests.length,
            requests: this.requests
        }, null, 2));
        
        console.log(`[RequestCapture] 已将当前 ${this.requests.length} 个请求保存到临时文件`);
    }

    async stopCapture() {
        this.page.off('request', this._onRequest);
        this.page.off('response', this._onResponse);
        
        // 计算本次会话新捕获的请求/响应数量
        const capturedCount = Math.max(0, this.requests.length - (this.sessionStartIndex || 0));
        
        // 如果本次没有新请求，则跳过保存，减少IO开销
        if (capturedCount === 0) {
            console.log(`[RequestCapture] 停止捕获：本次未捕获到新请求，跳过保存`);
            return;
        }
        
        // 停止捕获时确保将当前数据保存到主文件和临时文件
        try {
            // 仅在有新请求时自动保存到临时文件
            this._autoSaveToTempFile();
            
            // 也保存到主文件（如果有捕获到请求）
            if (this.requests.length > 0) {
                console.log(`[RequestCapture] 保存主请求文件...`);
                await this.saveResults(this.outputPath || './results_v1');
            }
            
            console.log(`[RequestCapture] 停止捕获，新增 ${capturedCount} 个请求/响应，总计 ${this.requests.length}`);
        } catch (error) {
            console.error(`[RequestCapture] 停止捕获时保存数据出错:`, error.message);
        }
    }
    
    cleanup() {
        // 清理定时器，避免内存泄漏
        if (this.autoSaveTimer) {
            clearInterval(this.autoSaveTimer);
            this.autoSaveTimer = null;
        }
    }

    _onRequest(request) {
        try {
            // 生成请求签名用于去重
            const signature = this._generateRequestSignature(request);
            
            // 检查是否为重复请求
            if (this.requestSignatures.has(signature)) {
                console.log(`[RequestCapture] 跳过重复请求: ${request.method()} ${request.url().substring(0, 80)}...`);
                return;
            }
            
            // 记录请求签名
            this.requestSignatures.add(signature);
            
            // 记录更详细的请求信息
            const requestData = {
                id: Math.random().toString(36).substring(2, 15),  // 生成唯一ID用于关联请求和响应
                type: 'request',
                url: request.url(),
                method: request.method(),
                resourceType: request.resourceType(),  // 资源类型 (document, xhr, fetch, script, stylesheet 等)
                headers: request.headers(),
                timestamp: Date.now(),
                context: this.context,
                signature: signature
            };
            
            // 如果是POST请求，尝试记录请求体
            if (request.method() === 'POST' || request.method() === 'PUT' || request.method() === 'PATCH') {
                const postData = request.postData();
                if (postData) {
                    requestData.postData = postData;
                    
                    // 尝试解析JSON请求体
                    try {
                        if (request.headers()['content-type'] && 
                            request.headers()['content-type'].includes('application/json')) {
                            requestData.jsonData = JSON.parse(postData);
                        }
                    } catch (e) {
                        // 解析JSON失败，保存原始数据
                        requestData.parseError = e.message;
                    }
                }
            }
            
            console.log(`[RequestCapture] 捕获请求: ${request.method()} ${request.url().substring(0, 100)}...`);
            this.requests.push(requestData);
            
            // 每捕获10个请求，自动保存一次数据到临时文件
            if (this.requests.length % 10 === 0) {
                this._autoSaveToTempFile();
            }
        } catch (e) {
            console.error(`[RequestCapture] 捕获请求时出错:`, e.message);
        }
    }
    
    /**
     * 生成请求签名用于去重
     * @param {Object} request - Puppeteer请求对象
     * @returns {string} 请求签名
     */
    _generateRequestSignature(request) {
        try {
            const url = new URL(request.url());
            const method = request.method();
            
            // 移除动态查询参数（如时间戳、随机数等）
            const staticParams = new URLSearchParams();
            url.searchParams.forEach((value, key) => {
                // 跳过常见的动态参数
                if (!['timestamp', '_', 't', 'random', 'nocache', '_t'].includes(key.toLowerCase())) {
                    staticParams.append(key, value);
                }
            });
            
            // 构建签名：方法 + 路径 + 静态参数
            const signatureParts = [
                method,
                url.pathname,
                staticParams.toString()
            ];
            
            // 对于 POST/PUT/PATCH 请求，也包含请求体
            if (['POST', 'PUT', 'PATCH'].includes(method)) {
                const postData = request.postData();
                if (postData) {
                    signatureParts.push(postData);
                }
            }
            
            return signatureParts.join('|');
        } catch (e) {
            // 如果生成签名失败，返回完整URL作为签名
            return `${request.method()}|${request.url()}`;
        }
    }

    async _onResponse(response) {
        try {
            const req = response.request();
            const requestId = this.requests.find(r => 
                r.type === 'request' && 
                r.url === response.url() && 
                r.method === req.method()
            )?.id;
            
            // 根据Content-Type判断响应体类型
            const contentType = response.headers()['content-type'] || '';
            let body = '';
            let parsedBody = null;
            
            // 对于JSON和文本格式的响应，获取响应体
            if (contentType.includes('application/json') || 
                contentType.includes('text/') || 
                contentType.includes('application/xml') ||
                contentType.includes('application/javascript')) {
                body = await response.text().catch(() => '');
                
                // 尝试解析JSON响应
                if (contentType.includes('application/json')) {
                    try {
                        parsedBody = JSON.parse(body);
                    } catch (e) {
                        // JSON解析失败，继续使用原始文本
                    }
                }
            }
            
            const responseData = {
                id: Math.random().toString(36).substring(2, 15),
                requestId: requestId, // 关联到请求
                type: 'response',
                url: response.url(),
                status: response.status(),
                statusText: response.statusText(),
                headers: response.headers(),
                method: req.method(),
                contentType: contentType,
                timestamp: Date.now(),
                context: this.context,
                size: (body && body.length) || 0
            };
            
            // 只为文本类型响应添加响应体，避免二进制数据
            if (body) {
                responseData.body = body.substring(0, 50000); // 限制大小，避免内存问题
                if (parsedBody) {
                    responseData.jsonData = parsedBody;
                    
                    // 检测是否为 list query 接口
                    responseData.isListQuery = this._isListQueryResponse(response, parsedBody);
                }
            }
            
            console.log(`[RequestCapture] 捕获响应: ${response.status()} ${response.url().substring(0, 100)}...`);
            this.requests.push(responseData);
            
            // 每捕获10个响应，自动保存一次数据到临时文件
            if (this.requests.length % 10 === 0) {
                this._autoSaveToTempFile();
            }
        } catch (e) {
            console.error(`[RequestCapture] 捕获响应时出错:`, e.message);
        }
    }

    filterBackendRequests(requests) {
        // 简单过滤：只保留XHR/Fetch等后端通信请求
        return requests.filter(r => /xhr|fetch/i.test(r.headers['sec-fetch-mode'] || ''));
    }

    setContext(context) {
        this.context = context;
    }

    saveResults(outputPath) {
        console.log(`[RequestCapture] 保存结果到 ${outputPath}...`);
        const fs = require('fs');
        const path = require('path');
        
        // 确保输出路径有效
        outputPath = outputPath || this.outputPath || './results_v1';
        
        // 检查是否有请求需要保存
        if (!this.requests || this.requests.length === 0) {
            console.log(`[RequestCapture] 警告: 没有捕获到任何请求，跳过保存操作`);
            return;
        }
        
        // 定义在外部，这样在try-catch块之外也能访问这些变量
        const requestsFolder = path.join(outputPath, 'http-requests');
        let analysisResults = { uniqueEndpoints: 0, totalRequests: 0 };
        const requestsByContext = {};
        
        // 按上下文分组请求（提前执行，避免在try块内执行后无法在外部访问）
        this.requests.forEach(req => {
            const context = req.context || 'unknown';
            if (!requestsByContext[context]) {
                requestsByContext[context] = [];
            }
            requestsByContext[context].push(req);
        });
        
        try {
            // 创建输出目录（如果不存在）
            if (!fs.existsSync(outputPath)) {
                fs.mkdirSync(outputPath, { recursive: true });
            }
            
            console.log(`[RequestCapture] 开始保存 ${this.requests.length} 个请求/响应到 ${outputPath}`);
            
            // 确保子目录存在
            try {
                if (!fs.existsSync(requestsFolder)) {
                    fs.mkdirSync(requestsFolder, { recursive: true });
                }
            } catch (dirError) {
                console.error(`[RequestCapture] 无法创建子目录 ${requestsFolder}:`, dirError.message);
                // 继续执行，至少保存主文件
            }
            
            // 分析请求模式
            analysisResults = this.analyzeRequests();
            
            // 保存请求分析结果
            const analysisFile = path.join(outputPath, 'http-analysis.json');
            fs.writeFileSync(analysisFile, JSON.stringify(analysisResults, null, 2));
            console.log(`[RequestCapture] 保存请求分析结果到 ${analysisFile}`);
            
            // 保存按上下文分组的请求数据
            const mainOutFile = path.join(outputPath, 'http-requests.json');
            fs.writeFileSync(mainOutFile, JSON.stringify({
                total: this.requests.length,
                byContext: Object.keys(requestsByContext).map(context => ({
                    context,
                    count: requestsByContext[context].length
                })),
                requestsAnalysis: {
                    uniqueEndpoints: analysisResults.uniqueEndpoints,
                    totalRequests: analysisResults.totalRequests
                },
                allRequests: this.requests
            }, null, 2));
            console.log(`[RequestCapture] 保存主请求数据到 ${mainOutFile}`);
            
            // 保存详细的请求数据（按上下文分组）
            if (!fs.existsSync(requestsFolder)) {
                fs.mkdirSync(requestsFolder, { recursive: true });
            }
        } catch (error) {
            console.error(`[RequestCapture] 保存请求数据时发生错误:`, error);
            // 即使分析失败，也尝试保存原始请求数据
            const errorBackupFile = path.join(outputPath, 'http-requests-backup.json');
            fs.writeFileSync(errorBackupFile, JSON.stringify({
                timestamp: new Date().toISOString(),
                errorMessage: error.message,
                totalRequests: this.requests.length,
                allRequests: this.requests
            }, null, 2));
            console.log(`[RequestCapture] 已将原始请求数据保存到备份文件 ${errorBackupFile}`);
            
            // 在出错的情况下也确保requestsFolder存在
            if (!fs.existsSync(requestsFolder)) {
                fs.mkdirSync(requestsFolder, { recursive: true });
            }
        }
        
        // 为每个上下文创建单独的文件
        try {
            Object.entries(requestsByContext).forEach(([context, requests]) => {
                // 替换非法文件名字符
                const safeContext = context.replace(/[\/\\?%*:|"<>]/g, '-');
                const contextFile = path.join(requestsFolder, `${safeContext}.json`);
                fs.writeFileSync(contextFile, JSON.stringify({
                    context,
                    timestamp: new Date().toISOString(),
                    count: requests.length,
                    requests
                }, null, 2));
            });
        } catch (error) {
            console.error(`[RequestCapture] 保存上下文请求数据时出错:`, error.message);
        }
        
        // 创建人类可读的请求摘要文件
        let summaryFile;
        try {
            summaryFile = path.join(outputPath, 'http-summary.md');
            const summary = this._generateReadableSummary(analysisResults || { uniqueEndpoints: 0, totalRequests: 0 }, requestsByContext);
            fs.writeFileSync(summaryFile, summary);
        } catch (error) {
            console.error(`[RequestCapture] 创建摘要文件时出错:`, error.message);
        }
        
        console.log(`[RequestCapture] 保存了 ${this.requests.length} 个HTTP请求到以下文件:`);
        console.log(`  - 主JSON数据: ${path.join(outputPath, 'http-requests.json')}`);
        console.log(`  - 请求分析: ${path.join(outputPath, 'http-analysis.json')}`);
        console.log(`  - 人类可读摘要: ${summaryFile || path.join(outputPath, 'http-summary.md')}`);
        console.log(`  - 详细请求文件夹: ${requestsFolder}/`);
    }

    analyzeRequests() {
        // 分析请求模式
        const patterns = {};
        const endpoints = {};
        
        // 按URL路径分组请求
        this.requests.filter(r => r.type === 'request').forEach(req => {
            try {
                const url = new URL(req.url);
                const path = url.pathname;
                
                // 记录路径模式
                if (!patterns[path]) {
                    patterns[path] = {
                        count: 0,
                        methods: {},
                        contexts: {}
                    };
                }
                
                patterns[path].count++;
                
                // 记录HTTP方法
                const method = req.method;
                if (!patterns[path].methods[method]) {
                    patterns[path].methods[method] = 0;
                }
                patterns[path].methods[method]++;
                
                // 记录调用上下文
                const context = req.context || 'unknown';
                if (!patterns[path].contexts[context]) {
                    patterns[path].contexts[context] = 0;
                }
                patterns[path].contexts[context]++;
                
                // 记录详细端点信息
                const endpointKey = `${method} ${path}`;
                if (!endpoints[endpointKey]) {
                    endpoints[endpointKey] = {
                        method,
                        path,
                        count: 0,
                        contexts: {},
                        params: new Set(),
                        headers: {},
                        samples: []
                    };
                }
                
                endpoints[endpointKey].count++;
                
                // 记录查询参数
                url.searchParams.forEach((value, key) => {
                    endpoints[endpointKey].params.add(key);
                });
                
                // 记录常见请求头
                ['content-type', 'authorization', 'x-requested-with'].forEach(header => {
                    if (req.headers[header]) {
                        if (!endpoints[endpointKey].headers[header]) {
                            endpoints[endpointKey].headers[header] = new Set();
                        }
                        endpoints[endpointKey].headers[header].add(req.headers[header]);
                    }
                });
                
                // 记录上下文
                if (!endpoints[endpointKey].contexts[context]) {
                    endpoints[endpointKey].contexts[context] = 0;
                }
                endpoints[endpointKey].contexts[context]++;
                
                // 保留样本请求
                if (endpoints[endpointKey].samples.length < 3) {
                    endpoints[endpointKey].samples.push({
                        url: req.url,
                        method: req.method,
                        postData: req.postData,
                        context: context
                    });
                }
            } catch (e) {
                console.warn(`[RequestCapture] 分析请求时出错: ${e.message}`);
            }
        });
        
        // 转换集合为数组以便序列化
        Object.values(endpoints).forEach(endpoint => {
            endpoint.params = Array.from(endpoint.params);
            
            // 转换 Set 为数组
            if (endpoint.headers) {
                Object.keys(endpoint.headers).forEach(header => {
                    if (endpoint.headers[header] instanceof Set) {
                        endpoint.headers[header] = Array.from(endpoint.headers[header]);
                    }
                });
            }
        });
        
        return {
            patternsByPath: patterns,
            endpointDetails: endpoints,
            uniqueEndpoints: Object.keys(endpoints).length,
            totalRequests: this.requests.filter(r => r.type === 'request').length
        };
    }

    _generateReadableSummary(analysisResults, requestsByContext) {
        // 生成人类可读的摘要
        const summary = [];
        
        // 添加标题
        summary.push('# HTTP 请求和响应捕获摘要');
        summary.push(`生成时间: ${new Date().toLocaleString()}`);
        summary.push('');
        
        // 概述
        summary.push('## 概述');
        summary.push(`- 总请求数: ${this.requests.length}`);
        summary.push(`- 独立端点数: ${analysisResults.uniqueEndpoints}`);
        summary.push('');
        
        // 按上下文分类
        summary.push('## 按上下文分类');
        Object.entries(requestsByContext).forEach(([context, requests]) => {
            summary.push(`### ${context}`);
            summary.push(`- 请求数量: ${requests.length}`);
            
            // 获取此上下文中的请求类型分布
            const methodCounts = {};
            requests.filter(r => r.type === 'request').forEach(req => {
                if (!methodCounts[req.method]) methodCounts[req.method] = 0;
                methodCounts[req.method]++;
            });
            
            summary.push('- HTTP方法分布:');
            Object.entries(methodCounts).forEach(([method, count]) => {
                summary.push(`  - ${method}: ${count}次`);
            });
            
            summary.push('');
        });
        
        // 显示主要端点
        summary.push('## 主要API端点');
        const endpoints = Object.values(analysisResults.endpointDetails)
            .sort((a, b) => b.count - a.count)
            .slice(0, 10); // 只显示前10个最常用的端点
        
        endpoints.forEach((endpoint, index) => {
            summary.push(`### ${index + 1}. ${endpoint.method} ${endpoint.path} (调用${endpoint.count}次)`);
            
            // 显示此端点的上下文分布
            summary.push('- 调用上下文:');
            Object.entries(endpoint.contexts)
                .sort((a, b) => b[1] - a[1])
                .forEach(([context, count]) => {
                    summary.push(`  - ${context}: ${count}次`);
                });
            
            // 显示查询参数
            if (endpoint.params.length > 0) {
                summary.push('- 查询参数:');
                endpoint.params.forEach(param => {
                    summary.push(`  - ${param}`);
                });
            }
            
            // 显示请求头
            summary.push('- 常见请求头:');
            Object.entries(endpoint.headers).forEach(([header, values]) => {
                summary.push(`  - ${header}: ${Array.isArray(values) ? values.join(', ') : values}`);
            });
            
            summary.push('');
        });
        
        // 添加使用说明
        summary.push('## 如何使用此数据');
        summary.push('- 详细JSON数据位于 `http-requests.json` 和 `http-requests` 文件夹中');
        summary.push('- 请求模式分析位于 `http-analysis.json` 中');
        summary.push('- 可根据上下文筛选请求，每个上下文的请求都保存在单独的JSON文件中');
        summary.push('');
        
        return summary.join('\n');
    }

    /**
     * 检测响应是否为 list query 接口
     * @param {Object} response - Puppeteer响应对象
     * @param {Object} parsedBody - 解析后的JSON响应体
     * @returns {boolean} 是否为 list query 接口
     */
    _isListQueryResponse(response, parsedBody) {
        try {
            const url = response.url();
            const req = response.request();
            
            // 检查 URL 参数是否包含分页参数
            const urlObj = new URL(url);
            const hasPaginationParams = ['page', 'limit', 'offset', 'size', 'pageSize', 'pageNum', 'per_page']
                .some(param => urlObj.searchParams.has(param));
            
            if (hasPaginationParams) {
                console.log(`[RequestCapture] 检测到 list query (分页参数): ${req.method()} ${url.substring(0, 80)}...`);
                return true;
            }
            
            // 检查响应体是否为数组结构
            if (parsedBody) {
                // 直接是数组
                if (Array.isArray(parsedBody)) {
                    console.log(`[RequestCapture] 检测到 list query (数组响应): ${req.method()} ${url.substring(0, 80)}...`);
                    return true;
                }
                
                // 嵌套数组（常见模式：data.list、data.items、result.data 等）
                const arrayFields = ['data', 'list', 'items', 'results', 'records', 'rows'];
                for (const field of arrayFields) {
                    if (parsedBody[field] && Array.isArray(parsedBody[field])) {
                        console.log(`[RequestCapture] 检测到 list query (嵌套数组 ${field}): ${req.method()} ${url.substring(0, 80)}...`);
                        return true;
                    }
                }
                
                // 检查是否包含分页元数据
                const paginationFields = ['total', 'totalCount', 'totalPages', 'pageCount', 'hasNext', 'hasMore'];
                const hasPaginationMetadata = paginationFields.some(field => field in parsedBody);
                
                if (hasPaginationMetadata) {
                    console.log(`[RequestCapture] 检测到 list query (分页元数据): ${req.method()} ${url.substring(0, 80)}...`);
                    return true;
                }
            }
            
            return false;
        } catch (e) {
            console.warn(`[RequestCapture] 检测 list query 时出错:`, e.message);
            return false;
        }
    }
    
    cleanup() {
        // 清理定时器，避免内存泄漏
        if (this.autoSaveTimer) {
            clearInterval(this.autoSaveTimer);
            this.autoSaveTimer = null;
            console.log(`[RequestCapture] 已清理自动保存定时器`);
        }
        
        // 清理去重集合
        if (this.requestSignatures) {
            this.requestSignatures.clear();
        }
    }
}

module.exports = RequestCapture;
