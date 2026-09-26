const puppeteer = require('puppeteer');
const fs = require('fs');
const os = require('os');
const path = require('path');

// 页面交互封装

class PageWrapper {
    constructor(optionsOrBrowser = null) {
        // 兼容两种传参方式：1) 传入 browser 实例；2) 传入配置对象 { mode }
        if (optionsOrBrowser && typeof optionsOrBrowser === 'object' && typeof optionsOrBrowser.newPage === 'function') {
            this.browser = optionsOrBrowser;
            this.options = {};
        } else {
            this.browser = null;
            this.options = optionsOrBrowser || {};
        }
        this.mode = this.options.mode || 'desktop';
        this.page = null;
    }

    _resolveExecutablePath() {
        const explicitPath = process.env.PUPPETEER_EXECUTABLE_PATH || process.env.BOLASCAN_CHROME_PATH;
        if (explicitPath && fs.existsSync(explicitPath)) {
            return explicitPath;
        }

        const homeDir = os.homedir();
        const chromeCacheDir = path.join(homeDir, '.cache', 'puppeteer', 'chrome');
        if (!fs.existsSync(chromeCacheDir)) {
            return null;
        }

        const candidates = [];
        const versions = fs.readdirSync(chromeCacheDir, { withFileTypes: true })
            .filter((entry) => entry.isDirectory())
            .map((entry) => entry.name);

        for (const version of versions) {
            const versionDir = path.join(chromeCacheDir, version);
            const macCandidate = path.join(
                versionDir,
                'chrome-mac-arm64',
                'Google Chrome for Testing.app',
                'Contents',
                'MacOS',
                'Google Chrome for Testing'
            );
            const macIntelCandidate = path.join(
                versionDir,
                'chrome-mac-x64',
                'Google Chrome for Testing.app',
                'Contents',
                'MacOS',
                'Google Chrome for Testing'
            );
            const linuxCandidate = path.join(versionDir, 'chrome-linux64', 'chrome');
            const winCandidate = path.join(versionDir, 'chrome-win64', 'chrome.exe');

            for (const candidate of [macCandidate, macIntelCandidate, linuxCandidate, winCandidate]) {
                if (fs.existsSync(candidate)) {
                    candidates.push(candidate);
                }
            }
        }

        if (candidates.length === 0) {
            return null;
        }

        candidates.sort().reverse();
        return candidates[0];
    }

    async init() {
        if (!this.browser) {
            const launchOptions = { 
                headless: false,
                // 兼容本地开发环境的跨私网请求与自签证书，避免因安全策略导致资源阻塞
                args: [
                    '--disable-features=BlockInsecurePrivateNetworkRequests',
                    '--allow-insecure-localhost',
                    '--ignore-certificate-errors',
                    '--no-sandbox',
                    '--disable-setuid-sandbox'
                ]
            };
            const executablePath = this._resolveExecutablePath();
            if (executablePath) {
                launchOptions.executablePath = executablePath;
                console.log(`[PageWrapper] 使用本地 Chrome 可执行文件: ${executablePath}`);
            }
            this.browser = await puppeteer.launch(launchOptions);
        }
        if (!this.page) {
            this.page = await this.browser.newPage();
            try {
                await this.page.evaluateOnNewDocument(() => {
                    try {
                        Object.defineProperty(window, 'open', {
                            configurable: true,
                            writable: true,
                            value: function(url) {
                                if (url && typeof url === 'string') {
                                    try { location.assign(url); } catch (_) {}
                                }
                                return null;
                            }
                        });
                    } catch (_) {}
                    const forceSameTabClick = (e) => {
                        try {
                            const link = e.target && e.target.closest ? e.target.closest('a[href]') : null;
                            if (!link) return;
                            const tgt = (link.getAttribute('target') || '').toLowerCase();
                            if (tgt === '_blank') {
                                e.preventDefault();
                                link.removeAttribute('target');
                                const href = link.getAttribute('href');
                                if (href) {
                                    try { location.assign(href); } catch (_) {}
                                }
                            }
                        } catch (_) {}
                    };
                    try { document.addEventListener('click', forceSameTabClick, true); } catch (_) {}
                    try {
                        const observer = new MutationObserver((mutations) => {
                            for (const m of mutations) {
                                if (m.addedNodes && m.addedNodes.length) {
                                    for (const node of m.addedNodes) {
                                        if (node && node.nodeType === 1 && node.querySelectorAll) {
                                            const anchors = node.querySelectorAll('a[target="_blank"]');
                                            anchors.forEach(a => { try { a.removeAttribute('target'); } catch (_) {} });
                                        }
                                    }
                                }
                                if (m.type === 'attributes' && m.attributeName === 'target') {
                                    const el = m.target;
                                    if (el && el.tagName === 'A' && (el.getAttribute('target') || '').toLowerCase() === '_blank') {
                                        try { el.removeAttribute('target'); } catch (_) {}
                                    }
                                }
                            }
                        });
                        observer.observe(document.documentElement, { subtree: true, childList: true, attributes: true, attributeFilter: ['target'] });
                    } catch (_) {}
                    // 屏蔽离站保存提示（beforeunload）
                    try {
                        const originalAddEventListener = window.addEventListener;
                        window.addEventListener = function(type, listener, options) {
                            if (String(type).toLowerCase() === 'beforeunload') {
                                // 阻止注册 beforeunload 监听以避免Chrome提示
                                return;
                            }
                            return originalAddEventListener.call(this, type, listener, options);
                        };
                        Object.defineProperty(window, 'onbeforeunload', {
                            configurable: true,
                            get() { return null; },
                            set(_) { /* 忽略 */ }
                        });
                        // 在捕获阶段抢先阻止后续监听器触发
                        window.addEventListener('beforeunload', function(e) {
                            try { e.stopImmediatePropagation(); } catch (_) {}
                        }, true);
                    } catch (_) {}
                });
            } catch (e) {
                console.warn(`[PageWrapper] 注入禁止新标签脚本失败: ${e.message}`);
            }
            try {
                this.page.on('popup', async (popupPage) => {
                    // 关闭新打开的标签页时不触发beforeunload，避免弹出保存提示
                    try { await popupPage.close({ runBeforeUnload: false }); } catch (_) {}
                });
                // 自动接受浏览器对话框，避免阻塞流程
                this.page.on('dialog', async (dialog) => {
                    try {
                        const msg = dialog.message() || '';
                        console.log(`[PageWrapper] 捕获到对话框(${dialog.type()}): ${msg.slice(0,80)}...`);
                        if (dialog.type() === 'prompt') {
                            await dialog.accept('');
                        } else {
                            await dialog.accept();
                        }
                    } catch (e) {
                        console.warn(`[PageWrapper] 处理对话框失败: ${e.message}`);
                        try { await dialog.dismiss(); } catch (_) {}
                    }
                });
            } catch (_) {}
            // 按模式配置页面环境（移动端）
            if (this.mode === 'mobile') {
                try {
                    const iPhone = puppeteer.devices && puppeteer.devices['iPhone 12'];
                    if (iPhone && typeof this.page.emulate === 'function') {
                        await this.page.emulate(iPhone);
                        console.log('[PageWrapper] 已启用 iPhone 12 模拟');
                    } else {
                        await this.page.setUserAgent('Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1');
                        await this.page.setViewport({ width: 390, height: 844, isMobile: true, hasTouch: true, deviceScaleFactor: 3 });
                        console.log('[PageWrapper] 已设置移动端 UA 与视口');
                    }
                } catch (e) {
                    console.warn(`[PageWrapper] 配置移动端环境失败: ${e.message}`);
                }
            }
        }
    }

    async goto(url) {
        // 确保页面已初始化，导航到指定URL，尽量避免因长连接导致的超时
        if (!this.page) {
            await this.init();
        }

        // 优先使用快速的 DOMContentLoaded，避免等待 networkidle 导致长时间卡住
        try {
            await this.page.goto(url, {
                waitUntil: 'domcontentloaded',
                timeout: 10000
            });
            console.log(`[PageWrapper] 页面加载完成: ${url} (domcontentloaded)`);
            return;
        } catch (error) {
            console.warn(`[PageWrapper] 首次导航失败: ${error.message}`);
        }

        // 回退：尝试等待 load 事件（部分页面更可靠）
        try {
            await this.page.goto(url, {
                waitUntil: 'load',
                timeout: 15000
            });
            console.log(`[PageWrapper] 使用 load 事件加载页面成功: ${url}`);
            return;
        } catch (retryError) {
            console.warn(`[PageWrapper] 使用 load 事件失败: ${retryError.message}`);
        }

        // 最后回退：不阻塞等待，发起导航并手动等待就绪状态
        try {
            const navPromise = this.page.goto(url, { timeout: 0 }); // 不设置等待条件，避免阻塞
            // 等待文档进入可交互或完成状态，最多 15s
            await Promise.race([
                this.page.waitForFunction(
                    () => document.readyState === 'interactive' || document.readyState === 'complete',
                    { timeout: 15000 }
                ).catch(() => false),
                new Promise(resolve => setTimeout(resolve, 5000)) // 保底等待，避免立即继续
            ]);
            await navPromise.catch(() => {});
            console.log(`[PageWrapper] 非阻塞导航完成: ${url}`);
        } catch (finalError) {
            console.error(`[PageWrapper] 页面加载彻底失败: ${finalError.message}`);
            // 继续执行，不抛出异常，以免中断整个扫描流程
        }
    }

    async getAllClickableElements() {
        // 在页面中查找所有可能可点击的元素，返回元素的基本信息（如标签、选择器、可见性等）
        if (!this.page) {
            await this.init();
        }
        const selectors = [
            'a[href]', 'button', 'input[type="button"]', 'input[type="submit"]',
            '[role="button"]', '[onclick]', '[tabindex]'
        ];
        return await this.page.$$eval(selectors.join(','), nodes =>
            nodes.map(node => ({
                tag: node.tagName,
                selector: node.outerHTML,
                visible: !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length),
                text: node.innerText || node.value || ''
            }))
        );
    }

    async clickElement(element) {
        // 根据传入的元素信息，定位并点击该元素，等待页面跳转或内容变化，返回点击结果和新URL
        if (!this.page) {
            await this.init();
        }
        
        console.log(`[PageWrapper] 尝试点击元素...`);
        let result = { clicked: false, newUrl: null, error: null };
        
        try {
            if (typeof element === 'string') {
                // 如果element是CSS选择器
                if (this.mode === 'mobile' && this.page.touchscreen) {
                    const coords = await this.page.evaluate((cssSelector) => {
                        const el = document.querySelector(cssSelector);
                        if (!el) return null;
                        try { el.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
                        const rect = el.getBoundingClientRect();
                        return { x: Math.floor(rect.left + rect.width / 2), y: Math.floor(rect.top + rect.height / 2) };
                    }, element);
                    if (coords) {
                        await this.page.touchscreen.tap(coords.x, coords.y);
                    } else {
                        await this.page.click(element);
                    }
                } else {
                    await this.page.click(element);
                }
                result.clicked = true;
            } else if (element && element.selector) {
                // 如果element是我们内部使用的元素对象格式
                const selector = element.selector;
                const isHtmlSelector = selector.trim().startsWith('<');
                
                if (isHtmlSelector) {
                    // 去噪：跳过资源/元信息标签
                    const lower = selector.toLowerCase();
                    if (/^\s*<\s*(link|script|meta|base|style)\b/.test(lower) || /\brel\s*=\s*["']stylesheet["']/.test(lower)) {
                        console.log('[PageWrapper] HTML选择器指向非交互资源/元信息元素，跳过点击');
                        result.clicked = false;
                        return result;
                    }
                    if (this.mode === 'mobile' && this.page.touchscreen) {
                        // 移动端：通过HTML匹配元素，计算坐标并tap
                        const coords = await this.page.evaluate((selectorHtml) => {
                            const tempDiv = document.createElement('div');
                            tempDiv.innerHTML = selectorHtml;
                            const tempEl = tempDiv.firstChild;
                            if (!tempEl) return null;
                            const matched = document.querySelectorAll(tempEl.tagName);
                            for (const el of matched) {
                                if (el.outerHTML === selectorHtml) {
                                    try { el.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
                                    const rect = el.getBoundingClientRect();
                                    return { x: Math.floor(rect.left + rect.width / 2), y: Math.floor(rect.top + rect.height / 2) };
                                }
                            }
                            return null;
                        }, selector);
                        if (coords) {
                            await this.page.touchscreen.tap(coords.x, coords.y);
                        } else {
                            // 回退直接click
                            await this.page.evaluate((selectorHtml) => {
                                const tempDiv = document.createElement('div');
                                tempDiv.innerHTML = selectorHtml;
                                const tempEl = tempDiv.firstChild;
                                if (!tempEl) return false;
                                const matched = document.querySelectorAll(tempEl.tagName);
                                for (const el of matched) {
                                    if (el.outerHTML === selectorHtml) { el.click(); return true; }
                                }
                                return false;
                            }, selector);
                        }
                    } else {
                        // 桌面端：直接 click
                        await this.page.evaluate((selectorHtml) => {
                            const tempDiv = document.createElement('div');
                            tempDiv.innerHTML = selectorHtml;
                            const tempEl = tempDiv.firstChild;
                            
                            if (!tempEl) return false;
                            
                            const matchedElements = document.querySelectorAll(tempEl.tagName);
                            for (const el of matchedElements) {
                                if (el.outerHTML === selectorHtml) {
                                    el.click();
                                    return true;
                                }
                            }
                            return false;
                        }, selector);
                    }
                    result.clicked = true;
                } else {
                    // CSS选择器
                    if (this.mode === 'mobile' && this.page.touchscreen) {
                        const coords = await this.page.evaluate((cssSelector) => {
                            const el = document.querySelector(cssSelector);
                            if (!el) return null;
                            try { el.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
                            const rect = el.getBoundingClientRect();
                            return { x: Math.floor(rect.left + rect.width / 2), y: Math.floor(rect.top + rect.height / 2) };
                        }, selector);
                        if (coords) {
                            await this.page.touchscreen.tap(coords.x, coords.y);
                        } else {
                            await this.page.click(selector);
                        }
                    } else {
                        await this.page.click(selector);
                    }
                    result.clicked = true;
                }
            }
            
            // 等待可能的导航完成
            try {
                await this.waitForNavigation();
                result.newUrl = await this.page.url();
            } catch (navError) {
                console.log(`[PageWrapper] 点击后没有发生导航，可能是页内交互`);
            }
        } catch (error) {
            console.error(`[PageWrapper] 点击元素时出错:`, error.message);
            result.error = error.message;
        }
        
        return result;
    }

    async fillInput(selector, value) {
        // 在页面中找到指定的输入框，并填充指定的内容
        if (!this.page) {
            await this.init();
        }
        
        try {
            if (typeof selector === 'string') {
                // 如果selector是CSS选择器
                await this.page.type(selector, value.toString());
                return true;
            } else if (selector && selector.selector) {
                // 如果selector是我们内部使用的元素对象格式
                const result = await this.page.evaluate((selectorHtml, inputValue) => {
                    const tempDiv = document.createElement('div');
                    tempDiv.innerHTML = selectorHtml;
                    const tempEl = tempDiv.firstChild;
                    
                    // 尝试查找匹配的元素
                    const matchedElements = document.querySelectorAll(tempEl.tagName);
                    for (const el of matchedElements) {
                        if (el.outerHTML === selectorHtml) {
                            el.value = inputValue;
                            // 触发input和change事件，模拟用户输入
                            const event = new Event('input', { bubbles: true });
                            el.dispatchEvent(event);
                            const changeEvent = new Event('change', { bubbles: true });
                            el.dispatchEvent(changeEvent);
                            return true;
                        }
                    }
                    return false;
                }, selector.selector, value.toString());
                return result;
            }
        } catch (error) {
            console.error(`[PageWrapper] 填充输入框时出错:`, error.message);
            return false;
        }
    }

    async getFormHtml(formElement) {
        // 提取目标表单的完整HTML（若给的是表单内元素，则取其所属表单）
        if (!this.page) {
            await this.init();
        }
        try {
            if (formElement && typeof formElement.selector === 'string') {
                const isHtmlSelector = formElement.selector.trim().startsWith('<');
                if (isHtmlSelector) {
                    // 已有表单或表单内元素的HTML，页面中查找匹配后返回表单HTML
                    const html = await this.page.evaluate((selectorHtml) => {
                        const tempDiv = document.createElement('div');
                        tempDiv.innerHTML = selectorHtml;
                        const tempEl = tempDiv.firstChild;
                        if (!tempEl) return selectorHtml;
                        const matched = document.querySelectorAll(tempEl.tagName);
                        for (const el of matched) {
                            if (el.outerHTML === selectorHtml) {
                                const form = el.tagName.toLowerCase() === 'form' ? el : el.closest('form');
                                return form ? form.outerHTML : el.outerHTML;
                            }
                        }
                        return selectorHtml;
                    }, formElement.selector);
                    return html || '';
                } else {
                    // CSS 选择器：定位元素或其所属表单并返回HTML
                    const html = await this.page.evaluate((cssSelector) => {
                        const el = document.querySelector(cssSelector);
                        if (!el) return '';
                        const form = el.tagName.toLowerCase() === 'form' ? el : el.closest('form');
                        return form ? form.outerHTML : el.outerHTML;
                    }, formElement.selector);
                    return html || '';
                }
            }
            return '';
        } catch (error) {
            console.warn('[PageWrapper] 获取表单HTML时出错:', error.message);
            return '';
        }
    }

    async submitForm(formElement, formData) {
        // 根据 LLM 生成的 formData 填充并提交表单
        if (!this.page) {
            await this.init();
        }
        const result = { success: false, newUrl: null };
        try {
            // 1) 解析目标表单句柄
            const formInfo = await this.page.evaluate((elementSelector) => {
                function resolveForm(el) {
                    if (!el) return null;
                    return el.tagName && el.tagName.toLowerCase() === 'form' ? el : el.closest('form');
                }
                if (!elementSelector) return { formFound: false };
                if (elementSelector.trim().startsWith('<')) {
                    // HTML 选择器：按外HTML匹配元素
                    const tempDiv = document.createElement('div');
                    tempDiv.innerHTML = elementSelector;
                    const tempEl = tempDiv.firstChild;
                    if (!tempEl) return { formFound: false };
                    const candidates = document.querySelectorAll(tempEl.tagName);
                    for (const el of candidates) {
                        if (el.outerHTML === elementSelector) {
                            const form = resolveForm(el);
                            return { formFound: !!form };
                        }
                    }
                    return { formFound: false };
                } else {
                    // CSS 选择器
                    const el = document.querySelector(elementSelector);
                    const form = resolveForm(el);
                    return { formFound: !!form };
                }
            }, formElement && formElement.selector ? formElement.selector : null);

            // 2) 按键值填充（键是 CSS 选择器）
            if (formData && typeof formData === 'object') {
                for (const [fieldSelector, value] of Object.entries(formData)) {
                    try {
                        await this.fillInput(fieldSelector, value);
                    } catch (e) {
                        console.warn(`[PageWrapper] 填充字段失败: ${fieldSelector} -> ${String(e && e.message || e)}`);
                    }
                }
            }

            // 3) 尝试点击提交按钮，否则直接 form.submit()
            let clickedSubmit = false;
            try {
                // 优先点击常见提交按钮
                const submitClicked = await this.page.evaluate((elementSelector) => {
                    function resolveForm(el) {
                        if (!el) return null;
                        return el.tagName && el.tagName.toLowerCase() === 'form' ? el : el.closest('form');
                    }
                    function clickIfExists(btn) {
                        if (!btn) return false;
                        try { btn.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
                        btn.click();
                        return true;
                    }
                    let form = null;
                    if (elementSelector && elementSelector.trim().startsWith('<')) {
                        const tempDiv = document.createElement('div');
                        tempDiv.innerHTML = elementSelector;
                        const tempEl = tempDiv.firstChild;
                        if (tempEl) {
                            const candidates = document.querySelectorAll(tempEl.tagName);
                            for (const el of candidates) {
                                if (el.outerHTML === elementSelector) { form = resolveForm(el); break; }
                            }
                        }
                    } else if (elementSelector) {
                        const el = document.querySelector(elementSelector);
                        form = resolveForm(el);
                    } else {
                        form = document.querySelector('form');
                    }
                    // 常见提交按钮选择器
                    const buttons = form ? Array.from(form.querySelectorAll('button, input[type="submit"], input[type="button"]')) :
                                           Array.from(document.querySelectorAll('button, input[type="submit"], input[type="button"]'));
                    // 优先类型为 submit
                    const primary = buttons.find(b => (b.tagName.toLowerCase() === 'button' && (b.type || '').toLowerCase() === 'submit') ||
                                                     (b.tagName.toLowerCase() === 'input' && (b.type || '').toLowerCase() === 'submit'));
                    if (clickIfExists(primary)) return true;
                    // 次优：按钮文本匹配
                    const textMatch = buttons.find(b => {
                        const t = (b.innerText || b.value || '').toLowerCase();
                        return ['submit','sign in','login','发送','提交','登录','注册','保存','下一步'].some(k => t.includes(k));
                    });
                    if (clickIfExists(textMatch)) return true;
                    // 兜底：直接 form.submit()
                    if (form) { try { form.submit(); return true; } catch (_) {} }
                    return false;
                }, formElement && formElement.selector ? formElement.selector : null);
                clickedSubmit = !!submitClicked;
            } catch (e) {
                console.warn('[PageWrapper] 尝试点击提交按钮时出错:', e.message);
            }

            // 4) 等待可能的导航或网络静默
            try {
                await this.waitForNavigation({ timeout: 6000 });
            } catch (_) {}
            result.newUrl = await this.page.url();
            result.success = true;
        } catch (error) {
            console.error('[PageWrapper] 提交表单时出错:', error.message);
            result.success = false;
        }
        return result;
    }

    async getFormErrorFeedback(formElement) {
        // 收集表单错误提示文本，供 LLM 修复数据
        if (!this.page) {
            await this.init();
        }
        try {
            const feedback = await this.page.evaluate((elementSelector) => {
                function resolveForm(el) {
                    if (!el) return null;
                    return el.tagName && el.tagName.toLowerCase() === 'form' ? el : el.closest('form');
                }
                let form = null;
                if (elementSelector && elementSelector.trim().startsWith('<')) {
                    const tempDiv = document.createElement('div');
                    tempDiv.innerHTML = elementSelector;
                    const tempEl = tempDiv.firstChild;
                    if (tempEl) {
                        const candidates = document.querySelectorAll(tempEl.tagName);
                        for (const el of candidates) {
                            if (el.outerHTML === elementSelector) { form = resolveForm(el); break; }
                        }
                    }
                } else if (elementSelector) {
                    const el = document.querySelector(elementSelector);
                    form = resolveForm(el);
                } else {
                    form = document.querySelector('form');
                }
                const root = form || document;
                const messages = [];
                // 常见错误提示容器
                const candidates = root.querySelectorAll('.error, .invalid, .help, .feedback, .message, [role="alert"], .ant-form-item-explain, .el-form-item__error');
                candidates.forEach(el => {
                    const text = (el.innerText || el.textContent || '').trim();
                    if (text) messages.push(text);
                });
                // HTML5 表单校验信息
                const invalidInputs = root.querySelectorAll('input:invalid, textarea:invalid, select:invalid');
                invalidInputs.forEach(el => {
                    const text = (el.validationMessage || '').trim();
                    if (text) messages.push(text);
                });
                return messages.join('\n');
            }, formElement && formElement.selector ? formElement.selector : null);
            return feedback || '';
        } catch (error) {
            console.warn('[PageWrapper] 获取表单错误反馈时出错:', error.message);
            return '';
        }
    }

    async waitForNavigation(options = {}) {
        // 等待页面发生导航（如跳转、刷新等），设置超时时间，防止长时间等待
        if (!this.page) {
            await this.init();
        }
        
        const defaultOptions = {
            timeout: 5000,  // 5秒超时
            waitUntil: 'networkidle2'
        };
        
        const mergedOptions = { ...defaultOptions, ...options };
        
        try {
            await this.page.waitForNavigation(mergedOptions);
            return true;
        } catch (error) {
            if (error.name === 'TimeoutError') {
                // 导航超时，可能是页内交互
                console.log(`[PageWrapper] 导航等待超时，可能没有页面跳转`);
                return false;
            }
            console.error(`[PageWrapper] 等待导航时出错:`, error.message);
            throw error;
        }
    }

    async evaluatePage(script) {
        // 在页面上下文中执行传入的脚本，返回执行结果
        // script 应为字符串，如 'document.documentElement.outerHTML'
        if (!this.page) {
            await this.init();
        }

        const maxRetries = 3;
        for (let attempt = 1; attempt <= maxRetries; attempt++) {
            try {
                // 在执行前等待页面达到可交互/完成状态，减少导航过程中的执行上下文销毁
                await this.page.waitForFunction(
                    () => document.readyState === 'interactive' || document.readyState === 'complete',
                    { timeout: 3000 }
                ).catch(() => {});

                const result = await this.page.evaluate(new Function('return ' + script));
                return result;
            } catch (error) {
                const msg = (error && error.message) ? error.message : String(error);
                const isContextDestroyed = msg.includes('Execution context was destroyed') ||
                                           msg.includes('Cannot find context with specified id') ||
                                           msg.includes('Target closed') ||
                                           msg.includes('Protocol error');
                if (!isContextDestroyed) {
                    // 其他错误直接抛出
                    throw error;
                }

                console.warn(`[PageWrapper] evaluatePage 上下文销毁，尝试第 ${attempt}/${maxRetries} 次重试: ${msg}`);

                // 等待可能的导航完成或页面稳定后再重试
                await Promise.race([
                    this.page.waitForNavigation({ timeout: 1500, waitUntil: 'networkidle2' }).catch(() => false),
                    this.page.waitForFunction(
                        () => document.readyState === 'interactive' || document.readyState === 'complete',
                        { timeout: 1500 }
                    ).catch(() => false),
                    new Promise(resolve => setTimeout(resolve, 500))
                ]).catch(() => {});

                // 若未达到最大重试次数则继续循环
                if (attempt === maxRetries) {
                    console.error('[PageWrapper] evaluatePage 多次重试仍失败，抛出错误');
                    throw error;
                }
            }
        }
    }

    async checkElementVisibility(element) {
        // 检查元素在页面上是否可见，避免对不可见元素进行操作
        if (!this.page) {
            await this.init();
        }
        
        try {
            // 使用puppeteer的isVisible方法检查元素可见性
            let isVisible = false;
            
            if (typeof element === 'string') {
                // 如果element是CSS选择器
                isVisible = await this.page.evaluate(selector => {
                    const el = document.querySelector(selector);
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length) && 
                           style.visibility !== 'hidden' && 
                           style.display !== 'none';
                }, element);
            } else if (element && element.selector) {
                // 如果element是我们内部使用的元素对象格式
                const selector = element.selector;
                console.log(`[PageWrapper] 检查元素可见性，选择器: ${selector.slice(0, 50)}...`);
                
                // 判断是HTML选择器还是CSS选择器
                const isHtmlSelector = selector.trim().startsWith('<');
                
                if (isHtmlSelector) {
                    // 旧的HTML选择器处理方式
                    isVisible = await this.page.evaluate(selectorHtml => {
                        const tempDiv = document.createElement('div');
                        tempDiv.innerHTML = selectorHtml;
                        const tempEl = tempDiv.firstChild;
                        
                        if (!tempEl) return false;
                        
                        // 尝试查找匹配的元素
                        const matchedElements = document.querySelectorAll(tempEl.tagName);
                        for (const el of matchedElements) {
                            if (el.outerHTML === selectorHtml) {
                                const style = window.getComputedStyle(el);
                                return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length) && 
                                       style.visibility !== 'hidden' && 
                                       style.display !== 'none';
                            }
                        }
                        return false;
                    }, selector);
                } else {
                    // 新的CSS选择器处理方式
                    isVisible = await this.page.evaluate(cssSelector => {
                        const el = document.querySelector(cssSelector);
                        if (!el) return false;
                        const style = window.getComputedStyle(el);
                        return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length) && 
                               style.visibility !== 'hidden' && 
                               style.display !== 'none';
                    }, selector);
                }
            }
            
            console.log(`[PageWrapper] 元素可见性检查结果: ${isVisible}`);
            return isVisible;
        } catch (error) {
            console.warn(`[PageWrapper] 检查元素可见性时出错: ${error.message}`);
            // 默认返回true，在出错的情况下尝试点击元素
            return true;
        }
    }

    async close() {
        if (this.page) {
            await this.page.close();
            this.page = null;
        }
        if (this.browser) {
            await this.browser.close();
            this.browser = null;
        }
    }
}

module.exports = PageWrapper;
