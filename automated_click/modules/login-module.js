class LoginModule {
    constructor(page, requestCapture, mode = 'desktop') {
        this.page = page;
        this.requestCapture = requestCapture;
        this.hasRefreshed = false; // 标记是否已经刷新过页面
        this.mode = mode || 'desktop';
        // 新增：登录重试计数器
        this.loginAttempts = 0;
        // 新增：验证码检测标志位，默认为 false
        this.captchaDetected = false;
    }

    async detectLoginForm() {
        // 检测页面是否存在登录表单（增强版：不再只局限于 form 内）
        const formInfo = await this.page.evaluate(() => {
            // 更广的密码输入选择器，兼容常见命名与占位符
            const pwdInput = document.querySelector(
                'input[type="password"], input[name*="password" i], input[id*="password" i], input[placeholder*="密码"], input[placeholder*="pass" i]'
            );
            let result = {
                hasPasswordInput: !!pwdInput,
                formStructure: null,
                buttonText: null,
                loginButtons: []
            };
            
            // 若密码输入存在于某个 form 中，则采集该 form 的结构
            if (pwdInput && pwdInput.form) {
                const form = pwdInput.form;
                const inputs = Array.from(form.querySelectorAll('input')).map(input => ({
                    type: input.type,
                    name: input.name,
                    id: input.id,
                    placeholder: input.placeholder
                }));
                
                // 获取 form 内所有可能的登录按钮
                const buttons = Array.from(form.querySelectorAll('button, input[type="submit"], input[type="button"]'));
                result.loginButtons = buttons.map(btn => ({
                    type: btn.tagName.toLowerCase(),
                    text: btn.innerText || btn.value || btn.textContent || '',
                    id: btn.id,
                    className: btn.className
                })).filter(btn => {
                    const text = (btn.text || '').toLowerCase();
                    return text.includes('login') || 
                           text.includes('log in') || 
                           text.includes('sign in') || 
                           text.includes('提交') || 
                           text.includes('登录');
                });
                
                const button = form.querySelector('button[type="submit"], input[type="submit"]');
                result.buttonText = button ? (button.innerText || button.value || '') : null;
                result.formStructure = inputs;
            } else {
                // 如果不在 form 中，尝试全局检索登录按钮
                const globalButtons = Array.from(document.querySelectorAll('button, input[type="submit"], input[type="button"], .ant-btn, [role="button"]'));
                result.loginButtons = globalButtons.map(btn => ({
                    type: btn.tagName.toLowerCase(),
                    text: btn.innerText || btn.value || btn.textContent || '',
                    id: btn.id,
                    className: btn.className
                })).filter(btn => {
                    const text = (btn.text || '').toLowerCase();
                    return text.includes('login') || 
                           text.includes('log in') || 
                           text.includes('sign in') || 
                           text.includes('提交') || 
                           text.includes('登录');
                });
            }
            
            return result;
        });
        
        console.log('[LoginModule] 表单分析:', JSON.stringify(formInfo, null, 2));
        return formInfo.hasPasswordInput;
    }

    async login(url, credentials) {
        console.log('[LoginModule] 跳转到登录页:', url);
        this.originalUrl = url;  // 保存原始URL以便后续比较
        // 仅当当前URL与目标URL不一致时才跳转，避免重复跳转导致上下文销毁
        try {
            const currentUrl = this.page.url();
            if (!currentUrl || (currentUrl && currentUrl !== url)) {
                await this.page.goto(url, { waitUntil: ['domcontentloaded', 'networkidle2'], timeout: 30000 });
            }
        } catch (e) {
            console.warn('[LoginModule] 跳转登录页时出错，尝试容错处理:', e.message);
            // 遇到 Execution context was destroyed 等错误时，尝试刷新
            try {
                await this.page.reload({ waitUntil: 'domcontentloaded' });
            } catch (e2) {
                console.warn('[LoginModule] 刷新登录页也失败:', e2.message);
            }
        }
        
        const hasForm = await this.detectLoginForm();
        console.log('[LoginModule] 检测到登录表单:', hasForm);
        if (!hasForm) return true; // 无需登录
        
        // 新增：自动登录重试，最多3次
        for (let attempt = 1; attempt <= 3; attempt++) {
            this.loginAttempts = attempt;
            await this.fillLoginForm(credentials);
            console.log('[LoginModule] 已填充表单');
            // 新增：如果检测到验证码，则切换为人工登录模式，跳过自动提交
            if (this.captchaDetected) {
                console.log('[LoginModule] 检测到验证码，跳过自动提交并等待人工登录...');
                const manualResult = await this.awaitManualLogin();
                return manualResult;
            }
            await this.submitLoginForm();
            console.log('[LoginModule] 已提交表单，等待验证...');
            const result = await this.verifyLogin();
            console.log('[LoginModule] 登录验证结果:', result);
            if (result) {
                // 登录成功，重置计数
                this.loginAttempts = 0;
                return true;
            }
            console.warn(`[LoginModule] 第 ${attempt}/3 次登录失败，准备重试...`);
            try {
                await this.page.waitForTimeout(1500);
                await this.page.reload({ waitUntil: ['domcontentloaded', 'networkidle2'] });
            } catch (e) {
                console.warn('[LoginModule] 重试前刷新失败:', e.message);
            }
        }

        // 连续3次失败：切换为人工登录模式
        console.warn('[LoginModule] 自动登录连续3次失败，切换人工登录模式：请在浏览器中手动点击完成登录，系统将自动检测登录成功并继续（最长等待5分钟）');
        const manualResult = await this.awaitManualLogin();
        return manualResult;
    }

    async fillLoginForm(credentials) {
        const userSelector = 'input[type="text"], input[name*="user" i], input[id*="user" i], input[type="email"], input[placeholder*="用户名"], input[placeholder*="user" i], input[placeholder*="email" i]';
        const passSelector = 'input[type="password"], input[name*="password" i], input[id*="password" i], input[placeholder*="密码"], input[placeholder*="pass" i]';
        const userInput = await this.page.$(userSelector);
        const passInput = await this.page.$(passSelector);
        if (userInput) {
            if (this.mode === 'mobile' && this.page.touchscreen) {
                try {
                    // 滚动到可见并点击输入框中心
                    await this.page.evaluate(el => { try { el.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {} }, userInput);
                    const box = await userInput.boundingBox();
                    if (box) {
                        await this.page.touchscreen.tap(Math.floor(box.x + box.width / 2), Math.floor(box.y + box.height / 2));
                    } else {
                        await userInput.click({ clickCount: 1 });
                    }
                } catch (_) {
                    await userInput.click({ clickCount: 1 });
                }
            } else {
                await userInput.click({ clickCount: 3 });
            }
            await userInput.type(credentials.username, { delay: 30 });
            // 触发input和change事件
            await this.page.evaluate(el => {
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }, userInput);
            console.log('[LoginModule] 用户名已输入');
        } else {
            console.log('[LoginModule] 未找到用户名输入框');
        }
        if (passInput) {
            if (this.mode === 'mobile' && this.page.touchscreen) {
                try {
                    await this.page.evaluate(el => { try { el.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {} }, passInput);
                    const box = await passInput.boundingBox();
                    if (box) {
                        await this.page.touchscreen.tap(Math.floor(box.x + box.width / 2), Math.floor(box.y + box.height / 2));
                    } else {
                        await passInput.click({ clickCount: 1 });
                    }
                } catch (_) {
                    await passInput.click({ clickCount: 1 });
                }
            } else {
                await passInput.click({ clickCount: 3 });
            }
            await passInput.type(credentials.password, { delay: 30 });
            await this.page.evaluate(el => {
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }, passInput);
            console.log('[LoginModule] 密码已输入');
        } else {
            console.log('[LoginModule] 未找到密码输入框');
        }

        // --- 新增：验证码人工输入支持 ---
        try {
            const captchaInputSelectors = [
                'input[name*="captcha" i]',
                'input[id*="captcha" i]',
                'input[placeholder*="验证码"]',
                'input[aria-label*="验证码"]',
                'input[placeholder*="驗證碼"]',
                'input[aria-label*="驗證碼"]'
            ];
            const hasCaptcha = await this.page.evaluate((sels) => {
                return sels.some(sel => {
                    const el = document.querySelector(sel);
                    return !!(el && (el.offsetParent !== null || getComputedStyle(el).visibility !== 'hidden'));
                });
            }, captchaInputSelectors);
            if (hasCaptcha) {
                // 标记：检测到验证码，后续切换为人工登录模式
                this.captchaDetected = true;
                console.log('[LoginModule] 检测到验证码输入框，切换为人工登录模式：请在浏览器中手动输入验证码并点击登录按钮。系统将等待登录成功，不会自动识别或提交。');
                // 尝试聚焦到验证码输入框并滚动到视野内
                await this.page.evaluate((sels) => {
                    for (const sel of sels) {
                        const el = document.querySelector(sel);
                        if (el) {
                            try { el.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
                            try { el.focus(); } catch (e) {}
                            break;
                        }
                    }
                }, captchaInputSelectors);
                try {
                    await this.page.waitForFunction((sels) => {
                        for (const sel of sels) {
                            const el = document.querySelector(sel);
                            if (el && typeof el.value === 'string' && el.value.trim().length > 0) {
                                return true;
                            }
                        }
                        return false;
                    }, { timeout: 120000 }, captchaInputSelectors);
                    console.log('[LoginModule] 检测到验证码已输入，等待用户手动点击登录...');
                } catch (e) {
                    console.warn('[LoginModule] 等待验证码输入超时或页面发生变化，将继续等待人工登录: ' + (e && e.message ? e.message : e));
                }
            }
        } catch (e) {
            console.warn('[LoginModule] 处理验证码输入逻辑时出错: ' + (e && e.message ? e.message : e));
        }
        // --- 新增逻辑结束 ---
    }

    async submitLoginForm() {
        // 新增保护：若检测到验证码，则跳过自动提交
        if (this.captchaDetected) {
            console.log('[LoginModule] 检测到验证码，跳过自动提交。请用户手动点击登录按钮。');
            return;
        }
        // 记录登录前的URL和标题
        const beforeUrl = this.page.url();
        const beforeTitle = await this.page.title();
        console.log('[LoginModule] 登录前URL:', beforeUrl);
        console.log('[LoginModule] 登录前标题:', beforeTitle);
        
        // 监听跳转
        let redirectDetected = false;
        let navigationPromise = this.page.waitForNavigation({ timeout: 8000, waitUntil: ['domcontentloaded', 'networkidle2'] })
            .catch(e => console.log('[LoginModule] 未检测到明确的页面跳转'));
        
        // 尝试多种方式找到并点击登录按钮
        try {
            // 1. 优先尝试 form 范围内的按钮
            const loginBtnSelector = [
                'form button:not([aria-label="close"]):not([type="reset"])',
                'form input[type="submit"]',
                'form .ant-btn',  // Ant Design按钮样式
                'form button.primary',
                'form button.submit',
                'form button:last-child', 
                '#t4-main-body > div > div.t4-col.col-12.col-xl > div.login.form-user.tm-main-x-small > form > fieldset > div.mb-4 > button'
            ].join(', ');
            
            let buttons = await this.page.$$(loginBtnSelector);
            console.log(`[LoginModule] form 范围内找到 ${buttons.length} 个可能的登录按钮`);
            
            // 如果 form 范围内没有，扩展到全局按钮
            if (!buttons || buttons.length === 0) {
                const globalLoginBtnSelector = [
                    // 常规按钮
                    'button:not([aria-label="close"]):not([type="reset"])',
                    'input[type="submit"]',
                    '.ant-btn',
                    '[role="button"]',
                    // uni-app / H5 常见可点击元素
                    'uni-button',
                    '.u-button',
                    'view[role="button"]',
                    'a[href="#"]',
                    'div[role="button"]',
                    // 兜底：所有 view 元素（后续过滤文本）
                    'view'
                ].join(', ');
                buttons = await this.page.$$(globalLoginBtnSelector);
                console.log(`[LoginModule] 全局范围找到 ${buttons.length} 个可能的登录按钮`);
            }
            
            let clicked = false;
            
            // 截图所有按钮，方便调试
            for (let i = 0; i < buttons.length; i++) {
                const btn = buttons[i];
                const box = await btn.boundingBox();
                if (!box) continue;
                
                // 获取按钮文本
                const text = await this.page.evaluate(el => el.innerText || el.value || '', btn);
                console.log(`[LoginModule] 按钮 ${i+1}: "${text}"`);
                
                // 优先点击明显的登录按钮
                const lower = (text || '').toLowerCase();
                if (!clicked && (
                    lower.includes('login') || 
                    lower.includes('log in') ||
                    lower.includes('sign in') ||
                    lower.includes('提交') ||
                    lower.includes('登录')
                )) {
                    console.log(`[LoginModule] 点击登录按钮: "${text}"`);
                    if (this.mode === 'mobile' && this.page.touchscreen) {
                        try {
                            await this.page.evaluate(el => { try { el.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {} }, btn);
                            const b = await btn.boundingBox();
                            if (b) {
                                await this.page.touchscreen.tap(Math.floor(b.x + b.width / 2), Math.floor(b.y + b.height / 2));
                            } else {
                                await btn.click();
                            }
                        } catch (_) {
                            await btn.click();
                        }
                    } else {
                        await btn.click();
                    }
                    clicked = true;
                }
            }
            
            // 如果没有明显的登录按钮，点击最后一个按钮（通常是提交按钮）
            if (!clicked && buttons.length > 0) {
                const lastBtn = buttons[buttons.length - 1];
                console.log('[LoginModule] 点击最后一个按钮（可能是提交按钮）');
                if (this.mode === 'mobile' && this.page.touchscreen) {
                    try {
                        await this.page.evaluate(el => { try { el.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {} }, lastBtn);
                        const b = await lastBtn.boundingBox();
                        if (b) {
                            await this.page.touchscreen.tap(Math.floor(b.x + b.width / 2), Math.floor(b.y + b.height / 2));
                        } else {
                            await lastBtn.click();
                        }
                    } catch (_) {
                        await lastBtn.click();
                    }
                } else {
                    await lastBtn.click();
                }
                clicked = true;
            }
            
            // 如果上述方法都失败，尝试传统的表单提交
            if (!clicked) {
                const formHandle = await this.page.$('form input[type="password"], form input[name*="password" i]');
                if (formHandle) {
                    const form = await this.page.evaluateHandle(el => el.form, formHandle);
                    if (form) {
                        console.log('[LoginModule] 使用表单submit方法提交');
                        await form.evaluate(f => f.submit());
                        clicked = true;
                    }
                }
            }
            
            // 如果仍然失败，尝试按Enter键
            if (!clicked) {
                console.log('[LoginModule] 尝试在表单上按Enter键');
                await this.page.keyboard.press('Enter');
            }
        } catch (e) {
            console.error('[LoginModule] 提交表单时出错:', e.message);
        }
        
        // 等待页面跳转或内容变化
        try {
            await navigationPromise;
        } catch (e) {
            // 使用fallback方法检测变化
        }
        
        // 再等待1秒，确保页面渲染
        await new Promise(r => setTimeout(r, 1000));
        
        // 检查URL和标题是否已变化
        const afterUrl = this.page.url();
        const afterTitle = await this.page.title(); 
        console.log('[LoginModule] 登录后URL:', afterUrl);
        console.log('[LoginModule] 登录后标题:', afterTitle);
        
        if (afterUrl !== beforeUrl) {
            console.log('[LoginModule] ✓ 检测到URL变化，可能已登录成功');
        }
        if (afterTitle !== beforeTitle) {
            console.log('[LoginModule] ✓ 检测到页面标题变化，可能已登录成功');
        }
        
        // 登录后截图，便于调试
        await this.page.screenshot({ path: './results/login-debug.png', fullPage: true });
        console.log('[LoginModule] 已截图 ./results/login-debug.png');
    }

    async verifyLogin() {
        // 更严格且更稳健的登录校验：加入网络层信号与更准确的URL判断
        try {
            const pageUrl = this.page.url();

            // 仅把明显的登录路径识别为“登录URL”，避免把通用的 /auth/* 误判为登录页
            const isLoginUrl = (() => {
                try {
                    const u = new URL(pageUrl);
                    const path = (u.pathname || '').toLowerCase();
                    const query = (u.search || '').toLowerCase();
                    const loginKeywords = ['login', 'signin', 'sign-in', '/pages/public/login'];
                    return loginKeywords.some(k => path.includes(k) || query.includes(k));
                } catch (_) { return false; }
            })();
            if (isLoginUrl) {
                console.log('[LoginModule] 仍在明显的登录URL（path/query包含login/signin），判定未登录');
                return false;
            }

            // 网络层登录成功信号（HTTP-only Cookie 或 Authorization）
            let networkOk = false;
            try {
                if (this.requestCapture && Array.isArray(this.requestCapture.requests)) {
                    const recent = this.requestCapture.requests.slice(-80); // 取最近若干条
                    const hasSetCookieSession = recent.some(r =>
                        r && r.type === 'response' && r.headers && typeof r.headers['set-cookie'] === 'string' &&
                        /(JSESSIONID|satoken|session|token)/i.test(r.headers['set-cookie'])
                    );
                    const hasAuthHeaderRequest = recent.some(r =>
                        r && r.type === 'request' && r.headers && (
                            !!r.headers['authorization'] || /bearer|token|satoken/i.test(JSON.stringify(r.headers || {}))
                        )
                    );
                    const has200Backend = recent.some(r => {
                        if (!(r && r.type === 'response')) return false;
                        const okStatus = r.status >= 200 && r.status < 300;
                        // 根据捕获到的关联请求判断是否XHR/Fetch（后端交互）
                        const req = recent.find(x => x && x.id === r.requestId && x.type === 'request');
                        const isBackend = /xhr|fetch/i.test((req && req.headers && req.headers['sec-fetch-mode']) || '');
                        // 排除明显的登录接口路径
                        const notLoginPath = (() => {
                            try {
                                const u = new URL(r.url);
                                const p = (u.pathname || '').toLowerCase();
                                return !(p.includes('login') || p.includes('signin') || p.includes('sign-in'));
                            } catch (_) { return true; }
                        })();
                        return okStatus && isBackend && notLoginPath;
                    });
                    networkOk = (hasSetCookieSession || hasAuthHeaderRequest) && has200Backend;
                    if (networkOk) {
                        console.log('[LoginModule] ✓ 网络信号表明已登录（Cookie/Authorization + 后端200响应）');
                    } else {
                        console.log('[LoginModule] 网络信号不足：', {
                            hasSetCookieSession,
                            hasAuthHeaderRequest,
                            has200Backend
                        });
                    }
                }
            } catch (e) {
                console.warn('[LoginModule] 检查网络信号时出错:', e.message);
            }

            if (networkOk) {
                return true;
            }

            // 页面UI/存储/Cookie信号
            const result = await this.page.evaluate(() => {
                // 1) UI 登录成功标志
                const successSelectors = [
                    '.user-profile', '.logout', '.avatar', '.user-menu',
                    '[href*="logout" i]', 'a[href*="logout" i]',
                    // 常见导航/布局作为弱信号（仅在登录输入不可见时才算）
                    'nav', '.navbar', '.ant-layout', '.ant-menu', '.sidebar'
                ];
                let uiSuccess = false;
                for (const selector of successSelectors) {
                    const el = document.querySelector(selector);
                    if (el && el.offsetParent !== null) { uiSuccess = true; break; }
                }

                // 2) 登录输入框是否仍可见
                const loginInputsVisible = (() => {
                    const sels = [
                        'input[type="password"]',
                        'input[name*="password" i]',
                        'input[id*="password" i]',
                        'input[placeholder*="密码"]',
                        'input[placeholder*="pass" i]',
                        'input[placeholder*="用户名"]',
                        'input[placeholder*="user" i]'
                    ];
                    for (const sel of sels) {
                        const el = document.querySelector(sel);
                        if (el && el.offsetParent !== null) return true;
                    }
                    return false;
                })();
                if (loginInputsVisible) {
                    return { ok: false, reason: 'login-inputs-visible' };
                }

                if (uiSuccess) {
                    return { ok: true, reason: 'ui-success' };
                }

                // 3) 存储中的token
                try {
                    const ls = window.localStorage;
                    const ss = window.sessionStorage;
                    const keys = ['token', 'Authorization'];
                    for (const k of keys) {
                        const v1 = ls && typeof ls.getItem === 'function' ? ls.getItem(k) : null;
                        const v2 = ss && typeof ss.getItem === 'function' ? ss.getItem(k) : null;
                        if ((v1 && v1.length > 0) || (v2 && v2.length > 0)) {
                            return { ok: true, reason: 'storage-token' };
                        }
                    }
                } catch (e) {}

                // 4) 非HTTP-only Cookie 的会话标识
                try {
                    const c = document.cookie || '';
                    if (c.includes('token=') || c.includes('satoken') || c.includes('JSESSIONID')) {
                        return { ok: true, reason: 'cookie-session' };
                    }
                } catch (e) {}

                return { ok: false, reason: 'no-success-signal' };
            });

            const ok = !!(result && result.ok);
            console.log(`[LoginModule] verifyLogin 判定: ${ok} (reason=${result && result.reason})`);
            return ok;
        } catch (e) {
            console.warn('[LoginModule] 验证登录状态时出错:', e.message);
            return false;
        }
    }

    // 新增：人工登录等待逻辑
    async awaitManualLogin() {
        try {
            // 尝试滚动到页面顶部，确保登录入口可见
            try { await this.page.evaluate(() => window.scrollTo(0, 0)); } catch (e) {}

            // 提示用户操作
            console.log('[LoginModule] 请在当前浏览器页面中手动完成登录（例如点击“登录/Sign in”等按钮并输入凭据），系统会自动检测登录成功。最长等待5分钟...');
            
            // 等待登录成功的多重信号：
            await this.page.waitForFunction(() => {
                // 1) UI 成功标志
                const successSelectors = ['.user-profile', '.logout', '.avatar', '.user-menu', '[href*="logout" i]', 'a[href*="logout" i]'];
                for (const selector of successSelectors) {
                    const el = document.querySelector(selector);
                    if (el && el.offsetParent !== null) {
                        return true;
                    }
                }
                // 2) 本地存储出现 token/Authorization
                try {
                    const ls = window.localStorage;
                    const ss = window.sessionStorage;
                    const keys = ['token', 'Authorization'];
                    for (const k of keys) {
                        const v1 = ls && typeof ls.getItem === 'function' ? ls.getItem(k) : null;
                        const v2 = ss && typeof ss.getItem === 'function' ? ss.getItem(k) : null;
                        if ((v1 && v1.length > 0) || (v2 && v2.length > 0)) return true;
                    }
                } catch (e) {}
                // 3) Cookie 出现常见会话标识
                try {
                    const c = document.cookie || '';
                    if (c.includes('token=') || c.includes('satoken') || c.includes('JSESSIONID')) return true;
                } catch (e) {}
                return false;
            }, { timeout: 5 * 60 * 1000 });

            console.log('[LoginModule] 检测到人工登录成功，继续扫描');
            try { await this.page.screenshot({ path: './results/login-success.png', fullPage: true }); } catch (e) {}
            // 重置登录计数器
            this.loginAttempts = 0;
            return true;
        } catch (e) {
            console.warn('[LoginModule] 等待人工登录超时或出错，继续流程: ' + (e && e.message ? e.message : e));
            return false;
        }
    }
}

module.exports = LoginModule;
