const { cssPath } = require('css-path');

class MainModule {
    constructor(options) {
        this.options = options;
        // 预留：初始化依赖模块（如URL管理器、页面封装、元素检测器等）
        // this.urlManager = ...
        // this.pageWrapper = ...
        // this.elementDetector = ...
        // this.resultManager = ...
        this.requestCapture = null; // 请求捕获器
    }

    /**
     * 快速并行扫描流程
     * @param {string} startUrl - 起始URL
     * @param {number} maxDepth - 最大深度
     */
    async scanFast(startUrl, maxDepth) {
        this._initModules(startUrl, maxDepth);
        this.originalLoginUrl = startUrl;
        
        let actualStartUrl = await this._performInitialSetup(startUrl);
        
        // 收集所有页面URL
        console.log('[MainModule-Fast] 开始收集页面URL...');
        const allUrls = await this._collectAllPageUrls(actualStartUrl, maxDepth);
        console.log(`[MainModule-Fast] 共收集到 ${allUrls.length} 个页面URL`);
        
        // 并行处理页面
        const parallelPages = this.options.parallelPages || 3;
        console.log(`[MainModule-Fast] 开始并行处理页面，并行数: ${parallelPages}`);
        
        for (let i = 0; i < allUrls.length; i += parallelPages) {
            const batch = allUrls.slice(i, Math.min(i + parallelPages, allUrls.length));
            console.log(`[MainModule-Fast] 处理批次 ${Math.floor(i / parallelPages) + 1}/${Math.ceil(allUrls.length / parallelPages)}, 包含 ${batch.length} 个URL`);
            
            await Promise.allSettled(
                batch.map(url => this._scanPageFast(url))
            );
        }
        
        // 保存结果
        await this._finalizeScan();
    }

    /**
     * 启动扫描流程
     * @param {string} startUrl - 起始URL
     * @param {number} maxDepth - 最大深度
     */
    async scan(startUrl, maxDepth) {
        this._initModules(startUrl, maxDepth);
        // 记录初始登录URL，供后续（如点击返回后）检测是否回到登录页
        this.originalLoginUrl = startUrl;
        // 登录处理逻辑（仅当用户提供credentials时才尝试登录）
        let actualStartUrl = startUrl; // 默认使用提供的startUrl作为扫描起点
        
        // 初始化请求捕获器（前移至首次 goto 之前）
        if (!this.requestCapture) {
            const RequestCapture = require('./request-capture');
            console.log(`[MainModule] 初始化请求捕获器，输出路径: ${this.options.outputPath || './results'}`);
            
            // 在初始化请求捕获器之前，确保页面已经初始化，避免 this.page 为空导致事件绑定失败
            await this.pageWrapper.init();
            // 挂载用户活动监控钩子
            await this._installUserActivityMonitor();
            
            // 如果用户提供了 token，则在任何导航前先设置全局请求头/Cookie
            if (this.options.token) {
                await this._applyTokenAuth();
            }
            
            this.requestCapture = new RequestCapture(this.pageWrapper.page);
            this.requestCapture.outputPath = this.options.outputPath || './results';
            console.log(`[MainModule] 请求捕获器状态: ${this.requestCapture ? '已初始化' : '初始化失败'}`);
        }
        
        // 先确保页面已初始化，并捕获初始加载请求
        if (this.requestCapture) {
            console.log(`[MainModule] 启动初始页面加载请求捕获`);
            await this.requestCapture.startCapture('initial-load', this.options.outputPath);
        }
        await this._awaitUserIdle('初始页面加载');
        await this.pageWrapper.goto(startUrl);
        
        // 导航到起始页面后，若配置了存储键，则把 token 写入 localStorage/sessionStorage
        if (this.options.token) {
            await this._injectTokenIntoStorage();
        }
        
        if (this.requestCapture) {
            console.log(`[MainModule] 停止初始页面加载请求捕获`);
            await this.requestCapture.stopCapture();
        }
        
        if (this.options.credentials && this.options.credentials.username && this.options.credentials.password) {
            const LoginModule = require('./login-module');
            this.loginModule = new LoginModule(this.pageWrapper.page, this.requestCapture, this.options?.mode || 'desktop'); // 传入请求捕获器并保存为实例变量                // 启动登录请求捕获
                if (this.requestCapture) {
                    console.log(`[MainModule] 启动登录请求捕获`);
                    await this.requestCapture.startCapture('login-detection', this.options.outputPath);
                }
            
            const needLogin = await this.loginModule.detectLoginForm();
            console.log('[MainModule] 检测到需要登录:', needLogin);
            
            // 停止登录检测的请求捕获
            if (this.requestCapture) {
                console.log(`[MainModule] 停止登录检测请求捕获`);
                await this.requestCapture.stopCapture();
            }
            
            if (needLogin) {
                // 启动登录过程的请求捕获
                if (this.requestCapture) {
                    console.log(`[MainModule] 启动登录过程请求捕获`);
                    await this.requestCapture.startCapture('login-process');
                }
                
                const loginSuccess = await this.loginModule.login(startUrl, this.options.credentials);
                
                // 停止登录过程的请求捕获
                if (this.requestCapture) {
                    console.log(`[MainModule] 停止登录过程请求捕获`);
                    await this.requestCapture.stopCapture();
                }
                
                if (!loginSuccess) {
                    console.warn('⚠️ 警告: 登录可能失败，但继续扫描...');
                    // 继续执行，不抛出异常
                    // throw new Error('登录失败，无法继续扫描');
                } else {
                    console.log('[MainModule] 登录成功！');
                    
                    // 额外验证：登录成功后回到首页，检查是否真正登录状态
                    console.log('[MainModule] 返回首页检查登录状态...');
                    
                    // 启动登录验证的请求捕获
                    if (this.requestCapture) {
                        console.log(`[MainModule] 启动登录验证请求捕获`);
                        await this.requestCapture.startCapture('login-verification');
                    }
                    
                    await this._awaitUserIdle('登录后返回首页');
                    await this.pageWrapper.goto(startUrl);
                    
                    // 登录验证回到首页后，确保存储中也有 token（如需要）
                    if (this.options.token) {
                        await this._injectTokenIntoStorage();
                    }
                    
                    // 停止登录验证请求捕获
                    if (this.requestCapture) {
                        console.log(`[MainModule] 停止登录验证请求捕获`);
                        await this.requestCapture.stopCapture();
                    }
                    
                    const isStillLoginPage = await this.pageWrapper.evaluatePage("!!document.querySelector('input[type=\"password\"], input[name*=\"password\" i], input[id*=\"password\" i], input[placeholder*=\"密码\"], input[placeholder*=\"pass\" i]')");
                    
                    if (isStillLoginPage) {
                        console.log('[MainModule] ⚠️ 警告: 返回首页后仍检测到登录表单，可能登录失败');
                    } else {
                        console.log('[MainModule] ✓ 确认登录成功！首页不再显示登录表单');
                        // 截图成功页面
                        await this.pageWrapper.page.screenshot({ path: './results/login-success.png', fullPage: true });
                        
                        // 获取当前URL作为真正的扫描起点
                        actualStartUrl = await this.pageWrapper.page.url();
                        console.log('[MainModule] 使用登录后URL作为扫描起点:', actualStartUrl);
                    }
                }
            }
        }
        
        // 使用actualStartUrl作为扫描起点，而不是原始的startUrl
        this.urlManager.addUrl(actualStartUrl, 0);
        console.log('[MainModule] 开始扫描，起点:', actualStartUrl);
        
        while (this.urlManager.hasMoreUrls()) {
            const { url, depth } = this.urlManager.getNextUrl();
            console.log(`[MainModule] 处理URL: ${url} (深度: ${depth}/${maxDepth})`);
            if (depth > maxDepth) {
                console.log(`[MainModule] 跳过URL: ${url} - 超过最大深度`);
                continue;
            }
            if (this.urlManager.hasUrlBeenProcessed(url)) {
                console.log(`[MainModule] 跳过URL: ${url} - 已处理过`);
                continue;
            }
            console.log(`[MainModule] 加载页面: ${url}`);
            
            // 在页面加载前启动请求捕获
            if (this.requestCapture) {
                console.log(`[MainModule] 启动页面加载请求捕获`);
                await this.requestCapture.startCapture(`page-load-${encodeURIComponent(url)}`);
            }
            
            await this._awaitUserIdle('页面加载');
            await this.pageWrapper.goto(url);
            
            // 导航到起始页面后，若配置了存储键，则把 token 写入 localStorage/sessionStorage
            if (this.options.token) {
                await this._injectTokenIntoStorage();
            }
            
            if (this.requestCapture) {
                console.log(`[MainModule] 停止页面加载请求捕获`);
                await this.requestCapture.stopCapture();
            }
            
            // 检测是否跳转回到初始登录URL
            const currentUrl = await this.pageWrapper.page.url();
            console.log(`[MainModule] 当前处理的URL: ${url}`);
            console.log(`[MainModule] 页面实际URL: ${currentUrl}`);
            console.log(`[MainModule] 初始登录URL: ${startUrl}`);
            
            // 若使用 token，跳过回到登录页检测与重新登录
            if (!this.options.token && this._isBackToLoginUrl(currentUrl, startUrl)) {
                console.log(`[MainModule] 检测到跳转回登录页面: ${currentUrl}`);
                console.log(`[MainModule] 触发重新登录流程...`);
                
                // 重新登录前等待空闲
                await this._awaitUserIdle('重新登录');
                // 重新登录
                const loginSuccess = await this._performReLogin(currentUrl);
                if (loginSuccess) {
                    console.log(`[MainModule] 重新登录成功，继续扫描`);
                    // 获取登录后的新URL作为当前处理的URL
                    const newUrl = await this.pageWrapper.page.url();
                    console.log(`[MainModule] 登录后URL: ${newUrl}`);
                } else {
                    console.log(`[MainModule] 重新登录失败，跳过当前URL`);
                    this.urlManager.markUrlProcessed(url);
                    continue;
                }
            }
            console.log(`[MainModule] 开始分析页面元素...`);
            await Promise.race([
                this.pageWrapper.page.waitForNavigation({ timeout: 1500, waitUntil: 'networkidle2' }).catch(() => false),
                this.pageWrapper.page.waitForFunction(
                    () => document.readyState === 'interactive' || document.readyState === 'complete',
                    { timeout: 1500 }
                ).catch(() => false),
                new Promise(resolve => setTimeout(resolve, 500))
            ]).catch(() => {});
            const htmlContent = await this.pageWrapper.evaluatePage('document.documentElement.outerHTML');
            
            // 获取页面上所有元素的CSS选择器
            let allElements;
            {
                const maxRetries = 3;
                for (let attempt = 1; attempt <= maxRetries; attempt++) {
                    try {
                        allElements = await this._getAllCssSelectors(this.pageWrapper.page);
                        break;
                    } catch (error) {
                        const msg = (error && error.message) ? error.message : String(error);
                        const isContextDestroyed = msg.includes('Execution context was destroyed') ||
                                                   msg.includes('Cannot find context with specified id') ||
                                                   msg.includes('Target closed') ||
                                                   msg.includes('Protocol error');
                        if (!isContextDestroyed) {
                            throw error;
                        }
                        console.warn(`[MainModule] 获取CSS选择器时上下文销毁，尝试第 ${attempt}/${maxRetries} 次重试: ${msg}`);
                        await Promise.race([
                            this.pageWrapper.page.waitForNavigation({ timeout: 1500, waitUntil: 'networkidle2' }).catch(() => false),
                            this.pageWrapper.page.waitForFunction(
                                () => document.readyState === 'interactive' || document.readyState === 'complete',
                                { timeout: 1500 }
                            ).catch(() => false),
                            new Promise(resolve => setTimeout(resolve, 500))
                        ]).catch(() => {});
                        if (attempt === maxRetries) {
                            console.error('[MainModule] 获取CSS选择器多次重试失败，抛出错误');
                            throw error;
                        }
                    }
                }
            }
            console.log(`[MainModule] 获取到 ${allElements.length} 个页面元素的CSS选择器`);
            
            // 过滤出可能是交互式的元素（排除资源/元信息标签，要求可见）
            let elements = (allElements || []).filter(item => {
                if (!item) return false;
                // 字段空值保护
                const isVisible = Boolean(item.isVisible);
                const isInteractive = Boolean(item.isInteractive) || this._isLikelyInteractive(item);
                const notResource = !this._isResourceOrMeta(item);
                return isVisible && notResource && isInteractive;
            });
            console.log(`[MainModule] 从中筛选出 ${elements.length} 个潜在可交互元素`);
            
            // 将CSS选择器转换为元素检测器期望的格式
            elements = elements.map(item => ({
                selector: item && item.selector,
                tag: item && item.tag,
                text: item && item.text,
                isVisible: item && item.isVisible,
                type: this._determineElementType(item)  // 添加一个辅助方法来确定元素类型
            }));
            
            // 我们仍然使用元素检测器的过滤和优先级功能
            elements = this.elementDetector.filterDuplicates(elements);
            elements = this.elementDetector.prioritizeElements(elements);
            // 对元素进行自定义分类
            const categories = {
                button: elements.filter(e => e.type === 'button'),
                link: elements.filter(e => e.type === 'link'),
                form: elements.filter(e => e.type === 'form'),
                container: elements.filter(e => e.type === 'container'),
                other: elements.filter(e => e.type === 'other')
            };
            
            console.log(`[MainModule] 元素分类统计: 按钮(${categories.button.length}), 链接(${categories.link.length}), 表单(${categories.form.length}), 容器(${categories.container.length}), 其他(${categories.other.length})`);
            const clickResults = [];
            const jumpUrls = [];
            const currentHost = (new URL(url)).host;
            await this._handleClickElements(categories, clickResults, jumpUrls, currentHost, url, depth);
            await this._handleFormElements(categories, clickResults, jumpUrls, currentHost, url, depth);
            this.resultManager.storeClickResults(url, clickResults);
            console.log(`[MainModule] 标记URL已处理: ${url}`);
            this.urlManager.markUrlProcessed(url);
            console.log(`[MainModule] 处理发现的跳转链接: ${jumpUrls.length} 个`);
            for (const jump of jumpUrls) {
                if (!this.urlManager.hasUrlBeenProcessed(jump.to)) {
                    console.log(`[MainModule] 添加新URL到队列: ${jump.to} (深度: ${depth + 1})`);
                    this.urlManager.addUrl(jump.to, depth + 1);
                } else {
                    console.log(`[MainModule] 跳过已处理的URL: ${jump.to}`);
                }
            }
        }
        console.log(`[MainModule] 所有URL已处理完毕`);
        
        // 保存捕获的请求数据
        if (this.requestCapture) {
            const outputPath = this.options.outputPath || './results';
            console.log(`[MainModule] 保存捕获的所有HTTP请求和响应数据...`);
            console.log(`[MainModule] 共捕获 ${this.requestCapture.requests.length} 个HTTP请求/响应数据`);
            
            // 在保存前打印请求类型统计
            if (this.requestCapture.requests.length > 0) {
                try {
                    const requestCount = this.requestCapture.requests.filter(r => r.type === 'request').length;
                    const responseCount = this.requestCapture.requests.filter(r => r.type === 'response').length;
                    const uniqueUrls = new Set(this.requestCapture.requests.map(r => r.url)).size;
                    
                    console.log(`[MainModule] HTTP请求统计: 请求(${requestCount}), 响应(${responseCount}), 唯一URL(${uniqueUrls})`);
                    
                    // 获取请求方法分布
                    const methodCounts = {};
                    this.requestCapture.requests
                        .filter(r => r.type === 'request')
                        .forEach(req => {
                            methodCounts[req.method] = (methodCounts[req.method] || 0) + 1;
                        });
                    
                    Object.entries(methodCounts).forEach(([method, count]) => {
                        console.log(`[MainModule] ${method} 请求: ${count} 个`);
                    });
                } catch (error) {
                    console.warn(`[MainModule] 生成请求统计时出错:`, error.message);
                }
            }
            
            // 保存请求数据
            this.requestCapture.saveResults(outputPath);
            console.log(`[MainModule] 请求数据已保存到以下文件:`);
            console.log(`[MainModule]  - 主文件: ${outputPath}/http-requests.json`);
            console.log(`[MainModule]  - 分析文件: ${outputPath}/http-analysis.json`);
            console.log(`[MainModule]  - 可读摘要: ${outputPath}/http-summary.md`);
            console.log(`[MainModule]  - 详细请求: ${outputPath}/http-requests/ 目录`);
        } else {
            console.warn(`[MainModule] 警告: 请求捕获器未初始化，无法保存HTTP请求数据`);
        }
        
        // 确保请求捕获器在关闭前保存所有数据
        if (this.requestCapture) {
            console.log(`[MainModule] 扫描结束，保存最终请求数据...`);
            await this.requestCapture.saveResults(this.options.outputPath);
            // 清理定时器
            this.requestCapture.cleanup();
        }
        
        console.log(`[MainModule] 关闭浏览器...`);
        await this.pageWrapper.close && this.pageWrapper.close();
        console.log(`[MainModule] 生成扫描报告...`);
        await this.resultManager.generateScanReport && this.resultManager.generateScanReport();
        
        // 安全获取已处理的URL数量
        let processedCount = 0;
        try {
            processedCount = this.urlManager.getProcessedCount();
        } catch (error) {
            console.warn(`[MainModule] 获取已处理URL数量失败:`, error.message);
        }
        
        console.log(`\n========================================`);
        console.log(`[MainModule] 扫描完成！总共处理了 ${processedCount} 个URL`);
        console.log(`[MainModule] 结果保存在: ${this.options.outputPath || './results'}/scan-report.json`);
        console.log(`========================================\n`);
    }

    _initModules(startUrl, maxDepth) {
        console.log(`[MainModule] 初始化核心模块...`);
        if (!this.urlManager) {
            const UrlManager = require('./url-manager');
            console.log(`[MainModule] 初始化URL管理器 (最大深度: ${maxDepth}, 基础URL: ${startUrl})`);
            
            // 加载配置文件
            let config = null;
            try {
                config = require('../config/config');
                console.log(`[MainModule] 成功加载配置文件`);
            } catch (error) {
                console.warn(`[MainModule] 加载配置文件失败，UrlManager将使用默认配置: ${error.message}`);
            }
            
            this.urlManager = new UrlManager({ 
                maxDepth, 
                baseUrl: startUrl,
                config: config
            });
        }
        if (!this.pageWrapper) {
            const PageWrapper = require('./page-wrapper');
            console.log(`[MainModule] 初始化页面包装器`);
            this.pageWrapper = new PageWrapper({ mode: this.options?.mode || 'desktop' });
        }
        if (!this.elementDetector) {
            const ElementDetector = require('./element-detector');
            console.log(`[MainModule] 初始化元素检测器`);
            this.elementDetector = new ElementDetector({});
        }
        if (!this.resultManager) {
            const ResultManager = require('../storage/result-manager');
            console.log(`[MainModule] 初始化结果管理器 (输出路径: ${this.options.outputPath || '默认'})`);
            this.resultManager = new ResultManager(this.options.outputPath);
        }
        // 请求捕获器会在页面初始化后再创建
        // RequestCapture的创建已移至scan方法中，确保page已经初始化
        console.log(`[MainModule] 所有核心模块初始化完成`);
    }

    async _handleClickElements(categories, clickResults, jumpUrls, currentHost, url, depth) {
        const SPANavigator = require('./spa-navigator');
        const spaNavigator = new SPANavigator(this.pageWrapper.page, this.options?.mode || 'desktop');
        // 计算所有可点击元素的总数，包括other类别中的可点击元素
        const totalElements = categories.button.length + categories.link.length + (categories.other ? categories.other.length : 0);
        console.log(`[MainModule] 准备测试 ${totalElements} 个可点击元素 (按钮 + 链接 + 其他可交互元素)`);
        let processedCount = 0;
        
        // 合并所有可点击元素：按钮、链接和其他可交互元素
        const allClickableElements = [
            ...categories.button,
            ...categories.link,
            ...(categories.other || [])
        ];
        
        for (const element of allClickableElements) {
            processedCount++;
            console.log(`[MainModule] 检查元素类型:`, JSON.stringify({
                类型: typeof element,
                是否为空: element === null,
                属性: element ? Object.keys(element).join(',') : 'N/A',
                选择器示例: element && element.selector ? element.selector.substring(0, 100) + '...' : 'N/A'
            }, null, 2));
            
            const isVisible = await this.pageWrapper.checkElementVisibility(element);
            console.log(`[MainModule] 元素可见性结果: ${isVisible}`);
            
            if (!isVisible) {
                console.log(`[MainModule] 跳过不可见元素 (${processedCount}/${totalElements})`);
                continue;
            }
            const beforeUrl = await this.pageWrapper.page.url();
            
            // 安全获取元素文本和类型
            let elementText = '未知文本';
            let elementType = '未知类型';
            try {
                if (element && element.selector) {
                    // 判断是CSS选择器还是HTML选择器
                    const isHtmlSelector = element.selector.trim().startsWith('<');
                    
                    if (isHtmlSelector) {
                        // 旧的HTML选择器处理方式
                        const elementInfo = await this.pageWrapper.page.evaluate((selectorHtml) => {
                            try {
                                const tempDiv = document.createElement('div');
                                tempDiv.innerHTML = selectorHtml;
                                const tempEl = tempDiv.firstChild;
                                
                                if (!tempEl) return { text: '无效HTML', type: '未知' };
                                
                                // 尝试查找匹配的元素
                                const matchedElements = document.querySelectorAll(tempEl.tagName);
                                for (const el of matchedElements) {
                                    if (el.outerHTML === selectorHtml) {
                                        return {
                                            text: el.textContent || el.innerText || el.outerHTML.slice(0, 50) + '...',
                                            type: el.tagName
                                        };
                                    }
                                }
                                
                                // 如果没找到精确匹配，返回临时元素的信息
                                return {
                                    text: tempEl.textContent || tempEl.innerText || tempEl.outerHTML.slice(0, 50) + '...',
                                    type: tempEl.tagName
                                };
                            } catch (err) {
                                return { text: '提取元素文本出错', type: '未知' };
                            }
                        }, element.selector);
                        
                        elementText = elementInfo.text;
                        elementType = elementInfo.type;
                    } else {
                        // 新的CSS选择器处理方式
                        const elementInfo = await this.pageWrapper.page.evaluate((cssSelector) => {
                            try {
                                const targetElement = document.querySelector(cssSelector);
                                if (targetElement) {
                                    return {
                                        text: targetElement.textContent || targetElement.innerText || targetElement.outerHTML.slice(0, 50) + '...',
                                        type: targetElement.tagName
                                    };
                                } else {
                                    return { text: '元素不存在', type: '未知' };
                                }
                            } catch (err) {
                                return { text: '提取元素文本出错', type: '未知' };
                            }
                        }, element.selector);
                        
                        elementText = elementInfo.text;
                        elementType = elementInfo.type;
                    }
                } else if (element && element.text) {
                    // 如果元素对象本身有text属性（来自新的_getAllCssSelectors方法）
                    elementText = element.text;
                    elementType = element.tag || '未知类型';
                }
            } catch (error) {
                console.warn(`[MainModule] 提取元素信息时出错:`, error.message);
            }
            
            const elementCategory = element.tag === 'BUTTON' || /button/i.test(element.selector) ? '按钮' :
                                   element.tag === 'A' ? '链接' :
                                   /menu-item|ant-menu-item/i.test(element.selector || '') ? '菜单项' :
                                   '其他可交互元素';
            
            console.log(`[MainModule] 测试点击元素(${processedCount}/${totalElements}): [${elementType}:${elementCategory}] "${elementText.trim()}"`);
            
            // 跳过不存在的元素，避免无效点击与请求捕获
            if (elementText === '元素不存在') {
                console.log(`[MainModule] 跳过不存在的元素 (${processedCount}/${totalElements})`);
                continue;
            }
            
            // 在点击前，若检测到用户正在操作，则等待空闲
            await this._awaitUserIdle('元素点击');

            // 在点击前启动请求捕获（仅当元素存在且可见）
            if (this.requestCapture && isVisible) {
                const elementId = `${elementType}-${elementCategory}-${elementText.trim().substring(0, 20)}`;
                console.log(`[MainModule] 启动请求捕获，元素ID: ${elementId}`);
                await this.requestCapture.startCapture(`click-${elementId}-${processedCount}`);
            }
            
            // 使用SPA智能点击，返回包括路由变化信息
            console.log(`[MainModule] 开始点击元素...`);
            let clickResult = await spaNavigator.smartClick(element);
            let afterUrl = clickResult && clickResult.newUrl ? clickResult.newUrl : await this.pageWrapper.page.url();
            
            // 点击后停止请求捕获
            if (this.requestCapture) {
                console.log(`[MainModule] 停止请求捕获`);
                await this.requestCapture.stopCapture();
            }
            
            console.log(`[MainModule] 元素点击完成，检查结果...`);
            
            // 识别元素类型的辅助函数
            const getElementTypeDescription = (element) => {
                if (!element) return '未知元素';
                
                const elTag = (element.tag || '').toLowerCase();
                const elSelector = (element.selector || '').toLowerCase();
                const elText = (element.text || '').trim();
                
                if (elTag === 'button' || /button/i.test(elSelector)) {
                    return `按钮 "${elText}"`;
                } else if (elTag === 'a' || /href/i.test(elSelector)) {
                    return `链接 "${elText}"`;
                } else if (/menu-item|ant-menu-item/i.test(elSelector)) {
                    return `菜单项 "${elText}"`;
                } else if (/dropdown|trigger/i.test(elSelector)) {
                    return `下拉菜单 "${elText}"`;
                } else if (elTag === 'li' && /role="menuitem"/i.test(elSelector)) {
                    return `导航项 "${elText}"`;
                } else {
                    return `可交互元素 "${elText}"`;
                }
            };
            
            // 检查是否打开了新标签页并记录
            if (clickResult && clickResult.newTabOpened) {
                const newTabUrl = clickResult.newTabUrl || '';
                let hostChanged = false;
                try {
                    if (newTabUrl) {
                        const afterHost = (new URL(newTabUrl)).host;
                        hostChanged = afterHost !== currentHost;
                    }
                } catch (e) {
                    console.warn(`[MainModule] 解析新标签页URL失败: ${e.message}`);
                }
                const elementDescription = getElementTypeDescription(element);
                jumpUrls.push({
                    from: beforeUrl,
                    to: newTabUrl || 'about:blank',
                    hostChanged,
                    element,
                    type: 'new-tab',
                    description: elementDescription,
                    title: clickResult.newTabTitle || null,
                    closed: clickResult.newTabClosed === true
                });
                console.log(`[MainModule] 检测到新标签页打开${clickResult.newTabClosed ? '并已自动关闭' : ''}: ${newTabUrl || '未知URL'}`);
                // 保留一份点击结果的记录
                clickResults.push({
                    element,
                    type: 'new-tab',
                    newTabInfo: {
                        url: newTabUrl || null,
                        title: clickResult.newTabTitle || null,
                        closed: clickResult.newTabClosed === true
                    }
                });
            }
            
            // 检查是否检测到弹窗
            if (clickResult && clickResult.hasPopup) {
                console.log(`[MainModule] 检测到点击后出现弹窗！`);
                if (clickResult.popupInfo) {
                    // 安全获取弹窗信息
                    const messageType = clickResult.popupInfo.messageType || '未知';
                    const popupText = clickResult.popupInfo.text || '未能获取弹窗文本';
                    const truncatedText = popupText.slice(0, 100) + (popupText.length > 100 ? '...' : '');
                    
                    console.log(`[MainModule] 弹窗类型: ${messageType}`);
                    console.log(`[MainModule] 弹窗内容: ${truncatedText}`);
                    
                    // 将弹窗信息记录到结果中
                    clickResults.push({
                        element,
                        type: 'popup',
                        popupInfo: clickResult.popupInfo
                    });
                    
                    // 可以在这里添加自动处理弹窗的逻辑，例如点击确定按钮等
                    try {
                        console.log(`[MainModule] 尝试自动处理弹窗...`);
                        if (this.options?.mode === 'mobile' && this.pageWrapper?.page?.touchscreen) {
                            const coords = await this.pageWrapper.page.evaluate(() => {
                                // 尝试查找一个可点击的“确定/确认”按钮并返回其中心坐标
                                const standardSelectors = [
                                    'button.ok', 
                                    'button.confirm', 
                                    '.btn-primary', 
                                    'button[type="submit"]', 
                                    '.confirm-btn', 
                                    '.modal-footer button',
                                    '.modal button',
                                    '.dialog button',
                                    '.popup button',
                                    'button.close',
                                    'button.btn-close',
                                    'button.btn-ok',
                                    '.modal-footer .btn'
                                ];
                                let candidate = null;
                                for (const selector of standardSelectors) {
                                    const btn = document.querySelector(selector);
                                    if (btn) { candidate = btn; break; }
                                }
                                if (!candidate) {
                                    const textMatches = ['确定', 'OK', '确认', 'Confirm', '是', 'Yes'];
                                    const buttons = document.querySelectorAll('button');
                                    for (const button of buttons) {
                                        const buttonText = (button.textContent || '').trim();
                                        if (textMatches.some(text => buttonText.includes(text))) {
                                            candidate = button; break;
                                        }
                                    }
                                }
                                if (candidate) {
                                    try { candidate.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
                                    const r = candidate.getBoundingClientRect();
                                    return { x: Math.floor(r.left + r.width / 2), y: Math.floor(r.top + r.height / 2) };
                                }
                                return null;
                            });
                            if (coords) {
                                await this.pageWrapper.page.touchscreen.tap(coords.x, coords.y);
                            } else {
                                // 坐标未获取到，回退到DOM click
                                await this.pageWrapper.page.evaluate(() => {
                                    const standardSelectors = [
                                        'button.ok', 
                                        'button.confirm', 
                                        '.btn-primary', 
                                        'button[type="submit"]', 
                                        '.confirm-btn', 
                                        '.modal-footer button',
                                        '.modal button',
                                        '.dialog button',
                                        '.popup button',
                                        'button.close',
                                        'button.btn-close',
                                        'button.btn-ok',
                                        '.modal-footer .btn'
                                    ];
                                    for (const selector of standardSelectors) {
                                        const button = document.querySelector(selector);
                                        if (button) { button.click(); return true; }
                                    }
                                    const textMatches = ['确定', 'OK', '确认', 'Confirm', '是', 'Yes'];
                                    const buttons = document.querySelectorAll('button');
                                    for (const button of buttons) {
                                        const buttonText = (button.textContent || '').trim();
                                        if (textMatches.some(text => buttonText.includes(text))) {
                                            button.click();
                                            return true;
                                        }
                                    }
                                    return false;
                                });
                            }
                        } else {
                            await this.pageWrapper.page.evaluate(() => {
                                // 尝试点击确定按钮 - 使用标准CSS选择器
                                const standardSelectors = [
                                    'button.ok', 
                                    'button.confirm', 
                                    '.btn-primary', 
                                    'button[type="submit"]', 
                                    '.confirm-btn', 
                                    '.modal-footer button',
                                    '.modal button',
                                    '.dialog button',
                                    '.popup button',
                                    'button.close',
                                    'button.btn-close',
                                    'button.btn-ok',
                                    '.modal-footer .btn'
                                ];
                                
                                // 标准选择器查找
                                for (const selector of standardSelectors) {
                                    const button = document.querySelector(selector);
                                    if (button) {
                                        console.log('找到确定按钮，点击中...');
                                        button.click();
                                        return true;
                                    }
                                }
                                
                                // 文本内容查找（无法使用:contains选择器，改为遍历）
                                const textMatches = ['确定', 'OK', '确认', 'Confirm', '是', 'Yes'];
                                const buttons = document.querySelectorAll('button');
                                for (const button of buttons) {
                                    const buttonText = button.textContent.trim();
                                    if (textMatches.some(text => buttonText.includes(text))) {
                                        console.log(`找到文本匹配的按钮: "${buttonText}"，点击中...`);
                                        button.click();
                                        return true;
                                    }
                                }
                                return false;
                            });
                        }
                        console.log(`[MainModule] 自动处理弹窗完成`);
                    } catch (error) {
                        console.warn(`[MainModule] 自动处理弹窗失败: ${error.message}`);
                    }
                }
            }
            
            // 检查是否发生传统跳转
            if (afterUrl && afterUrl !== beforeUrl) {
                const afterHost = (new URL(afterUrl)).host;
                const hostChanged = afterHost !== currentHost;
                const elementDescription = getElementTypeDescription(element);
                jumpUrls.push({
                    from: beforeUrl,
                    to: afterUrl,
                    hostChanged,
                    element,
                    type: 'traditional',
                    description: elementDescription
                });
                console.log(`[MainModule] 检测到页面跳转: ${beforeUrl} -> ${afterUrl}`);
                console.log(`[MainModule] 页面跳转类型: 传统跳转${hostChanged ? ' (跨域)' : ''} (由${elementDescription}触发)`);
            }
            
            // 检查SPA路由变化
            if (clickResult && clickResult.routeChanged) {
                const virtualUrl = await spaNavigator.getVirtualUrl();
                if (virtualUrl && virtualUrl !== beforeUrl) {
                    const elementDescription = getElementTypeDescription(element);
                    jumpUrls.push({
                        from: beforeUrl,
                        to: virtualUrl,
                        hostChanged: false,
                        element,
                        type: 'spa',
                        description: elementDescription
                    });
                    console.log(`[MainModule] 检测到SPA路由变化: ${beforeUrl} -> ${virtualUrl}`);
                    console.log(`[MainModule] 页面跳转类型: SPA路由变化 (无页面刷新) (由${elementDescription}触发)`);
                }
            }
            
            if (afterUrl === beforeUrl && (!clickResult || !clickResult.routeChanged)) {
                console.log(`[MainModule] 点击后URL未变化，可能是页内交互或无效点击`);
            }
            
            // 如果之前没有因为弹窗已经记录过结果，则现在记录
            // 需要先检查clickResult是否存在，以及是否有hasPopup属性
            if (!clickResult || !clickResult.hasPopup || !clickResults.some(r => r.element === element && r.type === 'popup')) {
                clickResults.push({ 
                    element, 
                    clickResult: clickResult || { success: false, error: "点击失败或没有结果" } 
                });
            }
            
            // 尝试返回原始URL，以便测试下一个元素
            if (afterUrl !== beforeUrl) {
                try {
                    console.log(`[MainModule] 返回原始页面: ${beforeUrl}`);
                    await this._awaitUserIdle('返回原始页面');
                    await this.pageWrapper.goto(beforeUrl);
                    await new Promise(r => setTimeout(r, 500)); // 等待页面加载
                    
                    // 返回原始页面后同样注入 token 到存储（若配置了存储键）
                    if (this.options.token) {
                        await this._injectTokenIntoStorage();
                    }
                    
                    // 返回原始页面后，检测是否回到了登录页（例如会话过期或被强制登出）
                    const currentUrlAfterReturn = await this.pageWrapper.page.url();
                    console.log(`[MainModule] 返回后实际URL: ${currentUrlAfterReturn}`);
                    const originalLoginUrl = this.originalLoginUrl || url;
                    console.log(`[MainModule] 初始登录URL(用于比较): ${originalLoginUrl}`);
                    if (!this.options.token && this._isBackToLoginUrl(currentUrlAfterReturn, originalLoginUrl)) {
                        console.log(`[MainModule] 返回后检测到回到登录页面，触发重新登录...`);
                        const reloginOk = await this._performReLogin(currentUrlAfterReturn);
                        if (reloginOk) {
                            console.log(`[MainModule] 重新登录成功(返回后)`);
                        } else {
                            console.log(`[MainModule] 重新登录失败(返回后)，跳过该元素后续操作`);
                            continue; // 跳过当前元素，继续后续元素
                        }
                    }
                } catch (error) {
                    console.warn(`[MainModule] 返回原始页面失败: ${error.message}`);
                }
            }
        }
    }

    async _handleFormElements(categories, clickResults, jumpUrls, currentHost, url, depth) {
        const LLMElementHelper = require('../llm/llm-element-helper');
        const llmHelper = new LLMElementHelper();
        console.log(`[MainModule] 准备测试 ${categories.form.length} 个表单元素`);
        let formCount = 0;
        for (const formElement of categories.form) {
            formCount++;
            console.log(`[MainModule] 处理表单 ${formCount}/${categories.form.length}`);
            const formHtml = await this.pageWrapper.getFormHtml(formElement);
            console.log(`[MainModule] 获取表单HTML成功，长度: ${formHtml.length} 字符`);
            console.log(`[MainModule] 使用LLM生成表单测试数据...`);
            
            // 尝试使用预生成的表单数据（如果有）
            let formData = {};
            if (this.elementDetector.formTestData && Object.keys(this.elementDetector.formTestData).length > 0) {
                console.log(`[MainModule] 使用预生成的表单数据`);
                formData = this.elementDetector.formTestData;
            } else {
                formData = await llmHelper.generateFormTestData(formHtml);
            }
            console.log(`[MainModule] 生成的表单数据:`, JSON.stringify(formData, null, 2));
            console.log(`[MainModule] 提交表单数据...`);

            // 若检测到用户操作中，等待空闲后再提交
            await this._awaitUserIdle('表单提交');
            
            // 在表单提交前启动请求捕获
            if (this.requestCapture) {
                const formId = `form-${formCount}`;
                console.log(`[MainModule] 启动请求捕获，表单ID: ${formId}`);
                await this.requestCapture.startCapture(`submit-${formId}`);
            }
            
            let submitResult = await this.pageWrapper.submitForm(formElement, formData);
            
            // 表单提交后停止请求捕获
            if (this.requestCapture) {
                console.log(`[MainModule] 停止请求捕获`);
                await this.requestCapture.stopCapture();
            }
            
            let retryCount = 0;
            while (!submitResult.success && retryCount < 2) {
                console.log(`[MainModule] 表单提交失败，第 ${retryCount+1} 次重试...`);
                const errorFeedback = await this.pageWrapper.getFormErrorFeedback(formElement);
                console.log(`[MainModule] 获取到表单错误反馈:`, errorFeedback);
                console.log(`[MainModule] 使用LLM修复表单数据...`);
                formData = await llmHelper.fixFormTestData(formHtml, formData, errorFeedback);
                console.log(`[MainModule] 修复后的表单数据:`, JSON.stringify(formData, null, 2));
                console.log(`[MainModule] 重新提交表单...`);
                
                // 重试提交前启动请求捕获
                if (this.requestCapture) {
                    const formId = `form-${formCount}-retry-${retryCount+1}`;
                    console.log(`[MainModule] 启动请求捕获，表单重试ID: ${formId}`);
                    await this.requestCapture.startCapture(`submit-retry-${formId}`);
                }

                // 若用户正在操作，等待空闲后再进行重试提交
                await this._awaitUserIdle('表单重试提交');
                
                submitResult = await this.pageWrapper.submitForm(formElement, formData);
                
                // 重试提交后停止请求捕获
                if (this.requestCapture) {
                    console.log(`[MainModule] 停止请求捕获`);
                    await this.requestCapture.stopCapture();
                }
                retryCount++;
            }
            if (submitResult.success) {
                console.log(`[MainModule] 表单提交成功!`);
            } else {
                console.log(`[MainModule] 表单提交仍然失败，达到最大重试次数`);
            }
            let afterUrl = submitResult && submitResult.newUrl ? submitResult.newUrl : await this.pageWrapper.page.url();
            console.log(`[MainModule] 表单提交后URL: ${afterUrl}`);
            if (afterUrl && afterUrl !== url) {
                const afterHost = (new URL(afterUrl)).host;
                const hostChanged = afterHost !== currentHost;
                jumpUrls.push({
                    from: url,
                    to: afterUrl,
                    hostChanged: hostChanged,
                    element: formElement,
                    type: 'form-submit'
                });
                console.log(`[MainModule] 检测到表单提交后页面跳转: ${url} -> ${afterUrl}${hostChanged ? ' (跨域)' : ''}`);
            } else {
                console.log(`[MainModule] 表单提交后URL未变化，可能是AJAX提交或页内处理`);
            }
            clickResults.push({ element: formElement, submitResult });
            console.log(`[MainModule] 表单处理完成，保存结果`);
        }
    }

    /**
     * 获取页面上所有潜在可点击元素的CSS选择器（高质量唯一选择器，模拟DevTools能力）
     * @param {Object} page - Puppeteer页面实例
     * @returns {Promise<Array>} - 返回CSS选择器数组
     */
    async _getAllCssSelectors(page) {
        console.log(`[MainModule] 开始获取页面所有CSS选择器（高质量模式）...`);
        
        try {
            // 在页面中执行脚本，生成高质量CSS选择器
            const results = await page.evaluate(() => {
                // 定义一个内部函数来生成CSS路径（模拟DevTools "Copy Selector"）
                function generateCssPath(element) {
                    if (!element) return null;
                    
                    // 如果有ID，直接使用ID（最高优先级）
                    if (element.id) {
                        const idSelector = `#${element.id}`;
                        // 验证ID选择器的唯一性
                        if (document.querySelectorAll(idSelector).length === 1) {
                            return idSelector;
                        }
                    }
                    
                    // 构建完整路径
                    const path = [];
                    let current = element;
                    
                    while (current && current !== document && current !== document.documentElement) {
                        let selector = current.tagName.toLowerCase();
                        
                        // 添加类名（如果有）
                        if (current.className && typeof current.className === 'string') {
                            const classes = current.className.trim().split(/\s+/)
                                .filter(cls => cls.length > 0 && !cls.includes(' '))
                                .join('.');
                            if (classes) {
                                selector += '.' + classes;
                            }
                        }
                        
                        // 检查在父级中的唯一性
                        if (current.parentElement) {
                            const siblings = Array.from(current.parentElement.children);
                            const sameTagSiblings = siblings.filter(sibling => 
                                sibling.tagName.toLowerCase() === current.tagName.toLowerCase()
                            );
                            
                            // 如果同类型兄弟节点多于1个，需要添加nth-child
                            if (sameTagSiblings.length > 1) {
                                const index = siblings.indexOf(current) + 1;
                                selector += `:nth-child(${index})`;
                            }
                            
                            // 验证当前级别的选择器唯一性
                            try {
                                const testPath = path.length > 0 ? 
                                    selector + ' > ' + path.join(' > ') : 
                                    selector;
                                const matches = current.parentElement.querySelectorAll(`:scope > ${selector}`);
                                if (matches.length !== 1 || matches[0] !== current) {
                                    // 如果仍不唯一，强制使用nth-child
                                    const index = siblings.indexOf(current) + 1;
                                    selector = selector.replace(/:nth-child\(\d+\)/, '') + `:nth-child(${index})`;
                                }
                            } catch (e) {
                                // 如果查询失败，保留原选择器
                            }
                        }
                        
                        path.unshift(selector);
                        current = current.parentElement;
                        
                        // 避免无限循环
                        if (path.length > 20) break;
                    }
                    
                    return path.join(' > ');
                }
                
                // 获取所有元素并生成选择器
                const allElements = document.querySelectorAll('*');
                const results = [];
                let debugCount = 0;
                
                for (const element of allElements) {
                    try {
                        const selector = generateCssPath(element);
                        if (selector) {
                            // 验证选择器的唯一性
                            const matchedElements = document.querySelectorAll(selector);
                            const isUnique = matchedElements.length === 1 && matchedElements[0] === element;
                            
                            if (isUnique) {
                                // 检查元素的基本属性
                                const style = window.getComputedStyle(element);
                                const isVisible = element.offsetWidth > 0 && 
                                                element.offsetHeight > 0 && 
                                                style.display !== 'none' && 
                                                style.visibility !== 'hidden';
                                
                                // 检查是否可能是交互式元素
                                const interactiveTags = ['A', 'BUTTON', 'INPUT', 'SELECT', 'TEXTAREA', 'LABEL', 'SUMMARY'];
                                let isInteractive = interactiveTags.includes(element.tagName);
                                
                                if (!isInteractive) {
                                    const role = element.getAttribute('role');
                                    if (role && ['button', 'link', 'menuitem', 'tab', 'checkbox', 'radio', 'switch', 'menu', 'menubar', 'option'].includes(role.toLowerCase())) {
                                        isInteractive = true;
                                    }
                                }
                                
                                if (!isInteractive) {
                                    if (style.cursor === 'pointer') isInteractive = true;
                                }
                                
                                if (!isInteractive) {
                                    const hasEventHandler = element.hasAttribute('onclick') || 
                                                          element.hasAttribute('ng-click') || 
                                                          element.hasAttribute('@click') || 
                                                          element.hasAttribute('v-on:click') || 
                                                          element.hasAttribute('data-toggle');
                                    if (hasEventHandler) isInteractive = true;
                                }
                                
                                // 调试信息：特别关注ant-menu相关元素
                                if (selector.includes('ant-menu') && debugCount < 5) {
                                    console.log(`[DEBUG] Ant Menu Element - Selector: ${selector}, Tag: ${element.tagName}, Classes: ${element.className}, Text: "${element.textContent?.trim().substring(0, 30)}"`, element);
                                    debugCount++;
                                }
                                
                                results.push({
                                    selector: selector,
                                    tag: element.tagName,
                                    text: (element.textContent || '').trim().substring(0, 50),
                                    isVisible: isVisible,
                                    isInteractive: isInteractive
                                });
                            } else {
                                // 调试：记录不唯一的选择器
                                if (selector.includes('ant-menu') && debugCount < 10) {
                                    console.warn(`[DEBUG] Non-unique selector: ${selector}, matched ${matchedElements.length} elements`);
                                    debugCount++;
                                }
                            }
                        }
                    } catch (err) {
                        // 单个元素失败不影响整体
                        try {
                            const cls = (typeof element.className === 'string')
                                ? element.className
                                : (element.className && typeof element.className.baseVal === 'string'
                                    ? element.className.baseVal
                                    : (element.getAttribute ? (element.getAttribute('class') || '') : ''));
                            if (cls && cls.includes('ant-menu')) {
                                console.error(`[DEBUG] Error processing ant-menu element:`, err, element);
                            }
                        } catch (_) {
                            // 防御性：忽略 className 的非字符串情况
                        }
                    }
                }
                
                return results;
            });
            
            console.log(`[MainModule] 获取到 ${results.length} 个元素的高质量CSS选择器`);
            return results;
        } catch (error) {
            console.error(`[MainModule] 获取CSS选择器时出错:`, error);
            return [];
        }
    }
    
    /**
     * 根据元素的特征确定其类型（按钮、链接、表单等）
     * @param {Object} element - 元素对象
     * @returns {string} - 元素类型
     */
    _determineElementType(element) {
        if (!element) return 'other';
        
        const tag = (element.tag || '').toLowerCase();
        const selector = (element.selector || '').toLowerCase();
        const text = (element.text || '').trim();
        
        // 资源/元信息标签直接归为其他（不作为交互元素处理）
        if (this._isResourceOrMeta(element)) {
            return 'other';
        }
        
        // 按钮类型元素
        if (tag === 'button' || 
            /button|btn|submit|reset|toggle/i.test(selector) || 
            /^\s*[\u4e00-\u9fa5]*[提交|确定|保存|确认|取消|登录][\u4e00-\u9fa5]*\s*$/i.test(text) ||
            /[role="button"]/i.test(selector)) {
            return 'button';
        }
        
        // 链接类型元素（仅限语义链接 <a> 或 ARIA role="link"）
        if (tag === 'a' || /\brole\s*=\s*["']link["']/i.test(selector)) {
            return 'link';
        }
        
        // 表单相关元素
        if (/input|select|textarea|form|checkbox|radio|switch|dropdown/i.test(tag) || 
            /input|select|textarea|form|checkbox|radio|switch|dropdown/i.test(selector)) {
            return 'form';
        }
        
        // 文章、卡片、面板等容器元素
        if (/card|post|article|panel|tile|item/i.test(selector) ||
            /ant-card|ant-list-item|ant-collapse-item/i.test(selector)) {
            return 'container';
        }
        
        // 未能确定类型的元素
        return 'other';
    }
    
    // 判断是否为资源/元信息标签（不参与交互扫描）
    _isResourceOrMeta(element) {
        if (!element) return false;
        try {
            const tag = (element.tag || '').toUpperCase();
            const excluded = ['LINK', 'SCRIPT', 'STYLE', 'META', 'BASE', 'TITLE', 'HEAD'];
            if (excluded.includes(tag)) return true;
            return false;
        } catch (e) {
            return false;
        }
    }
    
    /**
     * 根据元素的特征判断是否可能是交互式元素
     * @param {Object} element - 元素对象
     * @returns {boolean} - 是否可能是交互式元素
     */
    _isLikelyInteractive(element) {
        if (!element) return false;
        
        const tag = (element.tag || '').toUpperCase();
        const selector = (element.selector || '').toLowerCase();
        const text = (element.text || '').trim();
        
        // 直接排除资源/元信息标签
        if (["LINK","SCRIPT","META","BASE","STYLE","HEAD","TITLE"].includes(tag)) {
            return false;
        }
        
        // 检查类名中的提示词
        const interactiveClassPatterns = [
            'btn', 'button', 'nav', 'menu', 'click', 'select',
            'dropdown', 'tab', 'item', 'option', 'trigger', 'toggle', 'control',
            'ant-', 'mui', 'el-', 'mat-', 'interactive', 'action', 'active',
            'expand', 'collapse', 'submit', 'cancel', 'confirm', 'delete',
            'edit', 'save', 'add', 'remove', 'open', 'close', 'show', 'hide',
            'switch', 'checkbox', 'radio', 'slider', 'card'
        ];
        
        if (interactiveClassPatterns.some(pattern => selector.includes(pattern))) {
            return true;
        }
        
        // 常见的可点击文本模式
        const clickableTextPatterns = [
            '登录', '注册', '提交', '确定', '取消', '确认', '发送', '保存', 
            '删除', '编辑', '修改', '查看', '详情', '更多', '下一步', '上一步',
            '继续', '完成', '返回', '关闭', '打开', '展开', '收起'
        ];
        
        if (clickableTextPatterns.some(pattern => text.includes(pattern))) {
            return true;
        }
        
        // li元素通常是菜单项
        if (tag === 'LI' && /menu|nav|list|item/i.test(selector)) {
            return true;
        }
        
        // 特定的组件命名模式
        if (/ant-menu-item|el-menu-item|mui-item|li\.ant-menu-overflow-item/i.test(selector)) {
            return true;
        }
        
        // 特定的属性模式
        if (/href|ng-click|@click|v-on:click|onclick|data-action|data-target|data-toggle/i.test(selector)) {
            return true;
        }
        
        return false;
    }
    
    /**
     * 检测当前URL是否跳转回到初始登录URL
     * @param {string} currentUrl - 当前页面URL
     * @param {string} originalLoginUrl - 原始登录URL
     * @returns {boolean} 是否跳转回登录页面
     */
    _isBackToLoginUrl(currentUrl, originalLoginUrl) {
        // 若提供 token，则认为无需回到登录页判断
        if (this.options && this.options.token) {
            return false;
        }
        try {
            const current = new URL(currentUrl);
            const original = new URL(originalLoginUrl);
            
            // 检查域名是否匹配
            const isSameHost = current.host === original.host;
            // 检查路径是否完全匹配或当前为站点根路径（常见登录后或未登录重定向情况）
            const isSamePath = current.pathname === original.pathname;
            const isRootPath = current.pathname === '/' || current.pathname === '';
            
            // 登录关键词：同时考虑路径与查询参数
            const loginKeywords = ['login', 'signin', 'auth', 'authentication'];
            const lowerPath = current.pathname.toLowerCase();
            const lowerSearch = current.search.toLowerCase();
            const hasLoginKeyword = loginKeywords.some(keyword =>
                lowerPath.includes(keyword) || lowerSearch.includes(keyword)
            );
            
            console.log(`[MainModule] URL检测 - 当前: ${currentUrl}, 原始: ${originalLoginUrl}`);
            console.log(`[MainModule] 域名匹配: ${isSameHost}, 路径匹配: ${isSamePath}, 根路径: ${isRootPath}, 包含登录关键词: ${hasLoginKeyword}`);
            
            // 触发条件：
            // 1) 同域名且路径相同（直接返回登录页）
            // 2) 同域名且包含登录关键词（如 /user/login 等）
            // 3) 同域名且当前为站点根路径，但没有token（可能未登录或被登出）
            return (isSameHost && isSamePath) || (isSameHost && hasLoginKeyword) || (isSameHost && isRootPath);
        } catch (error) {
            console.log(`[MainModule] URL检测出错: ${error.message}`);
            return false;
        }
    }
    
    /**
     * 执行重新登录
     * @param {string} loginUrl - 登录页面URL
     * @returns {boolean} 登录是否成功
     */
    async _performReLogin(loginUrl) {
        try {
            // 检查是否有登录模块和凭据
            if (!this.loginModule) {
                console.log(`[MainModule] 未找到登录模块，无法重新登录`);
                return false;
            }
            
            if (!this.options.credentials) {
                console.log(`[MainModule] 未提供登录凭据，无法重新登录`);
                return false;
            }
            
            console.log(`[MainModule] 开始重新登录流程...`);
            const loginResult = await this.loginModule.login(loginUrl, this.options.credentials);
            
            if (loginResult) {
                console.log(`[MainModule] 重新登录成功`);
                return true;
            } else {
                console.log(`[MainModule] 重新登录失败`);
                return false;
            }
        } catch (error) {
            console.log(`[MainModule] 重新登录过程中出错: ${error.message}`);
            return false;
        }
    }
    // 在所有请求上携带用户提供的 token：通过 ExtraHTTPHeaders + 可选 Cookie
    async _applyTokenAuth() {
        try {
            const page = this.pageWrapper && this.pageWrapper.page;
            const { token, tokenHeader = 'Authorization', tokenPrefix = 'Bearer ', tokenCookieName, startUrl } = this.options || {};
            if (!token || !page) return;
            const headerValue = `${tokenPrefix || ''}${token}`;
            await page.setExtraHTTPHeaders({ [tokenHeader]: headerValue });
            console.log(`[MainModule] 已启用全局请求头携带 Token: ${tokenHeader}=<masked>`);
            if (tokenCookieName) {
                try {
                    await page.setCookie({ name: tokenCookieName, value: token, url: startUrl });
                    console.log(`[MainModule] 已设置 token Cookie: ${tokenCookieName}`);
                } catch (e) {
                    console.warn(`[MainModule] 设置 token Cookie 失败: ${e.message}`);
                }
            }
        } catch (e) {
            console.warn(`[MainModule] 设置全局 Token 头失败: ${e.message}`);
        }
    }

    // 将 token 写入 Web Storage，供页面脚本读取（如前端从 localStorage 读取 token）
    async _injectTokenIntoStorage() {
        try {
            const page = this.pageWrapper && this.pageWrapper.page;
            const { token, tokenStorageKey, tokenStorageTarget = 'local' } = this.options || {};
            if (!token || !tokenStorageKey || !page) return;
            await page.evaluate((key, value, target) => {
                try {
                    const store = target === 'session' ? window.sessionStorage : window.localStorage;
                    store.setItem(key, value);
                    return true;
                } catch (err) {
                    console.log('注入 token 到存储失败:', err && err.message);
                    return false;
                }
            }, tokenStorageKey, token, tokenStorageTarget);
            console.log(`[MainModule] 已注入 token 到 ${tokenStorageTarget === 'session' ? 'sessionStorage' : 'localStorage'} 键: ${tokenStorageKey}`);
        } catch (e) {
            console.warn(`[MainModule] 注入 token 到存储失败: ${e.message}`);
        }
    }

    // 用户活动监控：当用户有操作时暂停自动化，空闲后再继续
    async _installUserActivityMonitor() {
        try {
            const page = this.pageWrapper && this.pageWrapper.page;
            if (!page) return;
            this._userActivity = {
                lastTs: Date.now(),
                idleMs: Number(this.options?.userIdleMs) > 0 ? Number(this.options.userIdleMs) : 3000,
                active: false
            };
            await page.exposeFunction('__notifyUserActivity', () => {
                this._userActivity.lastTs = Date.now();
                this._userActivity.active = true;
            });
            await page.evaluateOnNewDocument(() => {
                const notify = () => {
                    try { typeof window.__notifyUserActivity === 'function' && window.__notifyUserActivity(); } catch (e) {}
                };
                const events = ['mousemove','mousedown','mouseup','click','keydown','keyup','wheel','touchstart','touchend','scroll','input','change','focus','blur'];
                events.forEach(ev => window.addEventListener(ev, notify, { passive: true, capture: false }));
            });
            if (!this._userActivityTimer) {
                this._userActivityTimer = setInterval(() => {
                    if (!this._userActivity) return;
                    const delta = Date.now() - (this._userActivity.lastTs || 0);
                    if (delta >= this._userActivity.idleMs) {
                        this._userActivity.active = false;
                    }
                }, 500);
            }
            console.log(`[MainModule] 已启用用户活动监控，空闲阈值: ${this._userActivity.idleMs}ms`);
        } catch (e) {
            console.warn(`[MainModule] 启用用户活动监控失败: ${e.message}`);
        }
    }

    async _awaitUserIdle(reason = '') {
        const page = this.pageWrapper && this.pageWrapper.page;
        const idleMs = (this._userActivity && this._userActivity.idleMs) || (Number(this.options?.userIdleMs) || 3000);
        const start = Date.now();
        let warned = false;
        while (true) {
            const last = (this._userActivity && this._userActivity.lastTs) || 0;
            const delta = Date.now() - last;
            if (delta >= idleMs) break;
            if (!warned) {
                console.log(`[MainModule] 检测到用户正在操作，暂停自动化${reason ? '：' + reason : ''}。等待空闲...`);
                warned = true;
            }
            const waitFor = Math.min(1000, idleMs - delta);
            try {
                await page.waitForTimeout(waitFor);
            } catch (_) {
                await new Promise(r => setTimeout(r, waitFor));
            }
        }
        if (warned) {
            const waited = Date.now() - start;
            console.log(`[MainModule] 用户空闲，恢复自动化${reason ? '：' + reason : ''}。等待时长 ${waited}ms`);
        }
    }

    /**
     * 执行初始设置（登录、token注入等）
     * @param {string} startUrl - 起始URL
     * @returns {Promise<string>} 实际的扫描起点URL
     */
    async _performInitialSetup(startUrl) {
        if (!this.requestCapture) {
            const RequestCapture = require('./request-capture');
            await this.pageWrapper.init();
            await this._installUserActivityMonitor();
            
            if (this.options.token) {
                await this._applyTokenAuth();
            }
            
            this.requestCapture = new RequestCapture(this.pageWrapper.page);
            this.requestCapture.outputPath = this.options.outputPath || './results';
        }
        
        let actualStartUrl = startUrl;
        
        if (this.requestCapture) {
            await this.requestCapture.startCapture('initial-load', this.options.outputPath);
        }
        await this._awaitUserIdle('初始页面加载');
        await this.pageWrapper.goto(startUrl);
        
        if (this.options.token) {
            await this._injectTokenIntoStorage();
        }
        
        if (this.requestCapture) {
            await this.requestCapture.stopCapture();
        }
        
        // 登录逻辑
        if (this.options.credentials && this.options.credentials.username && this.options.credentials.password) {
            const LoginModule = require('./login-module');
            this.loginModule = new LoginModule(this.pageWrapper.page, this.requestCapture, this.options?.mode || 'desktop');
            
            if (this.requestCapture) {
                await this.requestCapture.startCapture('login-detection', this.options.outputPath);
            }
            
            const needLogin = await this.loginModule.detectLoginForm();
            
            if (this.requestCapture) {
                await this.requestCapture.stopCapture();
            }
            
            if (needLogin) {
                if (this.requestCapture) {
                    await this.requestCapture.startCapture('login-process');
                }
                
                const loginSuccess = await this.loginModule.login(startUrl, this.options.credentials);
                
                if (this.requestCapture) {
                    await this.requestCapture.stopCapture();
                }
                
                if (loginSuccess) {
                    console.log('[MainModule] 登录成功！');
                    
                    if (this.requestCapture) {
                        await this.requestCapture.startCapture('login-verification');
                    }
                    
                    await this._awaitUserIdle('登录后返回首页');
                    await this.pageWrapper.goto(startUrl);
                    
                    if (this.options.token) {
                        await this._injectTokenIntoStorage();
                    }
                    
                    if (this.requestCapture) {
                        await this.requestCapture.stopCapture();
                    }
                    
                    actualStartUrl = await this.pageWrapper.page.url();
                    console.log('[MainModule] 使用登录后URL作为扫描起点:', actualStartUrl);
                }
            }
        }
        
        return actualStartUrl;
    }

    /**
     * 收集所有页面URL（广度优先遍历）
     * @param {string} startUrl - 起始URL
     * @param {number} maxDepth - 最大深度
     * @returns {Promise<Array>} URL列表
     */
    async _collectAllPageUrls(startUrl, maxDepth) {
        const urlsToScan = [];
        const visited = new Set();
        const queue = [{ url: startUrl, depth: 0 }];
        
        while (queue.length > 0 && urlsToScan.length < 100) { // 限制最多100个页面
            const { url, depth } = queue.shift();
            
            if (visited.has(url) || depth > maxDepth) continue;
            
            visited.add(url);
            urlsToScan.push(url);
            
            try {
                await this.pageWrapper.goto(url);
                
                // 快速提取页面上所有链接
                const links = await this.pageWrapper.page.evaluate(() => {
                    const anchors = document.querySelectorAll('a[href]');
                    return Array.from(anchors).map(a => a.href).filter(href => href.startsWith('http'));
                });
                
                const currentHost = (new URL(url)).host;
                
                for (const link of links) {
                    try {
                        const linkHost = (new URL(link)).host;
                        if (linkHost === currentHost && !visited.has(link)) {
                            queue.push({ url: link, depth: depth + 1 });
                        }
                    } catch (e) {
                        // 忽略无效URL
                    }
                }
            } catch (error) {
                console.warn(`[MainModule-Fast] 收集URL时出错: ${url}`, error.message);
            }
        }
        
        return urlsToScan;
    }

    /**
     * 快速扫描单个页面
     * @param {string} url - 页面URL
     */
    async _scanPageFast(url) {
        try {
            console.log(`[MainModule-Fast] 开始扫描页面: ${url}`);
            
            if (this.requestCapture) {
                await this.requestCapture.startCapture(`fast-scan-${encodeURIComponent(url)}`);
            }
            
            await this.pageWrapper.goto(url);
            
            if (this.options.token) {
                await this._injectTokenIntoStorage();
            }
            
            // 获取所有可交互元素
            const allElements = await this._getAllCssSelectors(this.pageWrapper.page);
            let elements = (allElements || []).filter(item => {
                if (!item) return false;
                const isVisible = Boolean(item.isVisible);
                const isInteractive = Boolean(item.isInteractive) || this._isLikelyInteractive(item);
                const notResource = !this._isResourceOrMeta(item);
                return isVisible && notResource && isInteractive;
            });
            
            elements = elements.map(item => ({
                selector: item && item.selector,
                tag: item && item.tag,
                text: item && item.text,
                isVisible: item && item.isVisible,
                type: this._determineElementType(item)
            }));
            
            elements = this.elementDetector.filterDuplicates(elements);
            elements = this.elementDetector.prioritizeElements(elements);
            
            console.log(`[MainModule-Fast] 页面 ${url} 发现 ${elements.length} 个可交互元素`);
            
            // 并行点击所有元素
            await this._clickElementsInParallel(elements, url);
            
            if (this.requestCapture) {
                await this.requestCapture.stopCapture();
            }
            
            console.log(`[MainModule-Fast] 完成扫描页面: ${url}`);
        } catch (error) {
            console.error(`[MainModule-Fast] 扫描页面失败: ${url}`, error.message);
        }
    }

    /**
     * 并行点击元素
     * @param {Array} elements - 元素列表
     * @param {string} pageUrl - 页面URL
     */
    async _clickElementsInParallel(elements, pageUrl) {
        const SPANavigator = require('./spa-navigator');
        const spaNavigator = new SPANavigator(this.pageWrapper.page, this.options?.mode || 'desktop');
        
        // 分批并行点击，每批10个元素
        const batchSize = 10;
        for (let i = 0; i < elements.length; i += batchSize) {
            const batch = elements.slice(i, Math.min(i + batchSize, elements.length));
            console.log(`[MainModule-Fast] 并行点击元素批次 ${Math.floor(i / batchSize) + 1}/${Math.ceil(elements.length / batchSize)}`);
            
            await Promise.allSettled(
                batch.map(async (element) => {
                    try {
                        const isVisible = await this.pageWrapper.checkElementVisibility(element);
                        if (!isVisible) return;
                        
                        // 快速点击，不等待
                        await spaNavigator.smartClick(element).catch(e => {
                            console.warn(`[MainModule-Fast] 点击元素失败:`, e.message);
                        });
                        
                        // 短暂延迟，让请求发出
                        await new Promise(r => setTimeout(r, 100));
                    } catch (error) {
                        // 忽略单个元素的失败
                    }
                })
            );
            
            // 批次之间短暂延迟
            await new Promise(r => setTimeout(r, 200));
            
            // 每批次后返回原页面
            try {
                await this.pageWrapper.goto(pageUrl);
            } catch (e) {
                console.warn(`[MainModule-Fast] 返回原页面失败`, e.message);
            }
        }
    }

    /**
     * 完成扫描并保存结果
     */
    async _finalizeScan() {
        if (this.requestCapture) {
            const outputPath = this.options.outputPath || './results';
            console.log(`[MainModule] 保存捕获的所有HTTP请求和响应数据...`);
            console.log(`[MainModule] 共捕获 ${this.requestCapture.requests.length} 个HTTP请求/响应数据`);
            
            if (this.requestCapture.requests.length > 0) {
                try {
                    const requestCount = this.requestCapture.requests.filter(r => r.type === 'request').length;
                    const responseCount = this.requestCapture.requests.filter(r => r.type === 'response').length;
                    const uniqueUrls = new Set(this.requestCapture.requests.map(r => r.url)).size;
                    
                    console.log(`[MainModule] HTTP请求统计: 请求(${requestCount}), 响应(${responseCount}), 唯一URL(${uniqueUrls})`);
                    
                    const methodCounts = {};
                    this.requestCapture.requests
                        .filter(r => r.type === 'request')
                        .forEach(req => {
                            methodCounts[req.method] = (methodCounts[req.method] || 0) + 1;
                        });
                    
                    Object.entries(methodCounts).forEach(([method, count]) => {
                        console.log(`[MainModule] ${method} 请求: ${count} 个`);
                    });
                } catch (error) {
                    console.warn(`[MainModule] 生成请求统计时出错:`, error.message);
                }
            }
            
            this.requestCapture.saveResults(outputPath);
            console.log(`[MainModule] 请求数据已保存到: ${outputPath}`);
        }
        
        if (this.requestCapture) {
            console.log(`[MainModule] 扫描结束，保存最终请求数据...`);
            await this.requestCapture.saveResults(this.options.outputPath);
            this.requestCapture.cleanup();
        }
        
        console.log(`[MainModule] 关闭浏览器...`);
        await this.pageWrapper.close && this.pageWrapper.close();
        
        console.log(`\n========================================`);
        console.log(`[MainModule] 快速扫描完成！`);
        console.log(`[MainModule] 结果保存在: ${this.options.outputPath || './results'}`);
        console.log(`========================================\n`);
    }
}

// 主模块，协调扫描流程
module.exports = MainModule;
