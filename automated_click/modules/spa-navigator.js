// SPA导航处理
class SPANavigator {
    constructor(page, mode = 'desktop') {
        this.page = page;
        this.framework = null;
        this.mode = mode || 'desktop';
    }

    async detectFramework() {
        // 简单检测主流SPA框架
        const framework = await this.page.evaluate(() => {
            if (window.__VUE__ || window.Vue) return 'vue';
            if (window.React || window.__REACT_DEVTOOLS_GLOBAL_HOOK__) return 'react';
            if (window.ng || window.getAllAngularRootElements) return 'angular';
            return null;
        });
        this.framework = framework;
        return framework;
    }

    async smartClick(element) {
        // 记录点击前的路由信息
        console.log(`[SPANavigator] 准备智能点击元素... (mode=${this.mode})`);
        const beforeRoute = await this.getVirtualUrl();
        console.log(`[SPANavigator] 点击前路由: ${beforeRoute}`);
        
        // 新标签页监控初始化：同时监听 browser 的 targetcreated 与 page 的 popup 事件
        const browser = this.page && typeof this.page.browser === 'function' ? this.page.browser() : null;
        let _targetCreated = null;
        let _popupPage = null;
        const _targetCreatedHandler = (target) => {
            try {
                if (target && typeof target.type === 'function' && target.type() === 'page') {
                    _targetCreated = target;
                }
            } catch (e) {}
        };
        const _popupHandler = (page) => { _popupPage = page; };
        try { if (browser && typeof browser.once === 'function') browser.once('targetcreated', _targetCreatedHandler); } catch (e) {}
        try { if (this.page && typeof this.page.once === 'function') this.page.once('popup', _popupHandler); } catch (e) {}
        
        if (!element) {
            console.warn(`[SPANavigator] 错误: 传入的元素为空`);
            return { success: false, error: '传入的元素为空', routeChanged: false };
        }
        
        try {
            // 检查element是否包含selector属性
            if (element && element.selector) {
                console.log(`[SPANavigator] 使用CSS选择器点击元素...`);
                
                // 检查selector是否是有效的字符串
                if (!element.selector || typeof element.selector !== 'string') {
                    throw new Error('无效的选择器: ' + (typeof element.selector));
                }
                
                // 记录选择器的基本信息用于调试
                const selectorInfo = element.selector.substring(0, 100) + (element.selector.length > 100 ? '...' : '');
                console.log(`[SPANavigator] CSS选择器: ${selectorInfo}`);
                
                // 判断选择器类型：如果以<开头，说明是HTML；否则是CSS选择器
                const isHtmlSelector = element.selector.trim().startsWith('<');
                
                if (isHtmlSelector) {
                    // 旧的HTML选择器处理方式（向后兼容）
                    await this.handleHtmlSelector(element.selector);
                } else {
                    // 新的CSS选择器处理方式（支持移动端tap），加入“祖先提升”与语义/样式可点击启发式
                    if (this.mode === 'mobile' && this.page.touchscreen) {
                        const points = await this.page.evaluate((cssSelector) => {
                            function isVisible(el) {
                                if (!el) return false;
                                const style = window.getComputedStyle(el);
                                const rect = el.getBoundingClientRect();
                                return rect.width > 0 && rect.height > 0 &&
                                       style.visibility !== 'hidden' &&
                                       style.display !== 'none' &&
                                       style.opacity !== '0';
                            }
                            function isInteractive(el) {
                                if (!el) return false;
                                const tag = (el.tagName || '').toLowerCase();
                                const style = window.getComputedStyle(el);
                                if (style.pointerEvents === 'none') return false;
                                if (tag === 'a' && el.hasAttribute('href')) return true;
                                if (tag === 'button') return true;
                                if (tag === 'input') {
                                    const type = (el.getAttribute('type') || '').toLowerCase();
                                    if (['button','submit','checkbox','radio'].includes(type)) return true;
                                }
                                const role = el.getAttribute('role');
                                if (role === 'button' || role === 'link' || role === 'tab') return true;
                                const tabindex = parseInt(el.getAttribute('tabindex') || '-1', 10);
                                if (!Number.isNaN(tabindex) && tabindex >= 0) return true;
                                if (typeof el.onclick === 'function') return true;
                                if (style.cursor === 'pointer') return true;
                                return false;
                            }
                            function isDecoration(el) {
                                if (!el) return false;
                                const tag = (el.tagName || '').toLowerCase();
                                if (tag === 'i' || tag === 'svg') return true;
                                const cls = (el.className || '').toString().toLowerCase();
                                if (cls.includes('icon') || cls.includes('badge') || cls.includes('arrow')) return true;
                                const rect = el.getBoundingClientRect();
                                const textLen = (el.textContent || '').trim().length;
                                if (textLen === 0 && rect.width <= 24 && rect.height <= 24) return true;
                                return false;
                            }
                            function pickTarget(startEl) {
                                let cur = startEl;
                                const maxDepth = 8;
                                const viewportArea = window.innerWidth * window.innerHeight;
                                const startRect = startEl.getBoundingClientRect();
                                for (let d = 0; d < maxDepth && cur; d++) {
                                    if (!isVisible(cur)) { cur = cur.parentElement; continue; }
                                    const rect = cur.getBoundingClientRect();
                                    const area = rect.width * rect.height;
                                    const heightOk = rect.height >= 28 && rect.height <= 140;
                                    const widthOk = rect.width >= Math.min(window.innerWidth * 0.5, 240);
                                    const grows = rect.width >= startRect.width * 1.5 || rect.height >= startRect.height * 1.5;
                                    if (isInteractive(cur) && !isDecoration(cur)) return cur;
                                    if (!isDecoration(cur) && heightOk && widthOk && grows && area < viewportArea * 0.85) {
                                        return cur;
                                    }
                                    const descendant = cur.querySelector('a[href], button, input[type="button"], input[type="submit"], [role="button"], [tabindex]');
                                    if (descendant && isVisible(descendant)) return descendant;
                                    const style = window.getComputedStyle(cur);
                                    if (style.pointerEvents === 'none') { cur = cur.parentElement; continue; }
                                    cur = cur.parentElement;
                                }
                                return startEl;
                            }
                            const start = document.querySelector(cssSelector);
                            if (!start) return null;
                            try { start.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
                            const target = pickTarget(start);
                            if (!target) return null;
                            const rect = target.getBoundingClientRect();
                            const cx = Math.floor(rect.left + rect.width / 2);
                            const cy = Math.floor(rect.top + rect.height / 2);
                            const leftMid = { x: Math.floor(rect.left + rect.width * 0.2), y: cy };
                            const rightMid = { x: Math.floor(rect.left + rect.width * 0.8), y: cy };
                            const topMid = { x: cx, y: Math.floor(rect.top + rect.height * 0.3) };
                            const bottomMid = { x: cx, y: Math.floor(rect.top + rect.height * 0.7) };
                            const center = { x: cx, y: cy };
                            // 优先返回中心点，再尝试左右/上下
                            return [center, leftMid, rightMid, topMid, bottomMid];
                        }, element.selector);
                        if (Array.isArray(points) && points.length) {
                            let success = false;
                            for (const pt of points) {
                                // 在坐标点击前，对选中的目标元素分派鼠标事件以提高兼容性
                                await this.page.evaluate((cssSelector) => {
                                    // 支持 Shadow DOM 的深度查询
                                    function deepQuerySelector(selector) {
                                        const tryRoot = (root) => (root ? root.querySelector(selector) : null);
                                        let found = tryRoot(document);
                                        if (found) return found;
                                        const stack = [];
                                        const all = document.querySelectorAll('*');
                                        all.forEach(el => { if (el.shadowRoot) stack.push(el.shadowRoot); });
                                        while (stack.length) {
                                            const root = stack.pop();
                                            found = tryRoot(root);
                                            if (found) return found;
                                            root.querySelectorAll('*').forEach(el => { if (el.shadowRoot) stack.push(el.shadowRoot); });
                                        }
                                        return null;
                                    }
                                    function isVisible(el) {
                                        if (!el) return false;
                                        const style = window.getComputedStyle(el);
                                        const rect = el.getBoundingClientRect();
                                        return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
                                    }
                                    function isInteractive(el) {
                                        if (!el) return false;
                                        const tag = (el.tagName || '').toLowerCase();
                                        const style = window.getComputedStyle(el);
                                        if (style.pointerEvents === 'none') return false;
                                        if (tag === 'a' && el.hasAttribute('href')) return true;
                                        if (tag === 'button') return true;
                                        const role = el.getAttribute('role');
                                        if (role === 'button' || role === 'link' || role === 'tab') return true;
                                        return false;
                                    }
                                    function pickTarget(startEl) {
                                        let cur = startEl;
                                        const maxDepth = 8;
                                        for (let d = 0; d < maxDepth && cur; d++) {
                                            if (!isVisible(cur)) { cur = cur.parentElement; continue; }
                                            if (isInteractive(cur)) return cur;
                                            const descendant = cur.querySelector('a[href], button, [role="button"], [tabindex]');
                                            if (descendant && isVisible(descendant)) return descendant;
                                            cur = cur.parentElement;
                                        }
                                        return startEl;
                                    }
                                    const start = deepQuerySelector(cssSelector);
                                    if (!start) return;
                                    const target = pickTarget(start);
                                    if (!target) return;
                                    const evtInit = { bubbles: true, cancelable: true, view: window };
                                    try {
                                        target.dispatchEvent(new TouchEvent('touchstart', evtInit));
                                        target.dispatchEvent(new PointerEvent('pointerdown', evtInit));
                                        target.dispatchEvent(new MouseEvent('mousedown', evtInit));
                                        target.dispatchEvent(new TouchEvent('touchend', evtInit));
                                        target.dispatchEvent(new MouseEvent('mouseup', evtInit));
                                        target.dispatchEvent(new MouseEvent('click', evtInit));
                                    } catch (e) {
                                        target.dispatchEvent(new Event('touchstart', evtInit));
                                        target.dispatchEvent(new Event('touchend', evtInit));
                                        target.dispatchEvent(new Event('click', evtInit));
                                    }
                                }, element.selector);
                                await this.page.touchscreen.tap(pt.x, pt.y);
                                await new Promise(r => setTimeout(r, 100));
                                const curRoute = await this.getVirtualUrl();
                                if (curRoute !== beforeRoute) { success = true; }
                            }
                            if (!success) {
                                // 兜底：直接在浏览器上下文触发点击，包含触摸事件序列
                                await this.page.evaluate((cssSelector) => {
                                    const deepQuerySelector = (selector) => {
                                        const tryRoot = (root) => root ? root.querySelector(selector) : null;
                                        let found = tryRoot(document);
                                        if (found) return found;
                                        const stack = [];
                                        const all = document.querySelectorAll('*');
                                        all.forEach(el => { if (el.shadowRoot) stack.push(el.shadowRoot); });
                                        while (stack.length) {
                                            const root = stack.pop();
                                            found = tryRoot(root);
                                            if (found) return found;
                                            root.querySelectorAll('*').forEach(el => { if (el.shadowRoot) stack.push(el.shadowRoot); });
                                        }
                                        return null;
                                    };
                                    const el = deepQuerySelector(cssSelector);
                                    if (!el) return;
                                    const evtInit = { bubbles: true, cancelable: true, view: window };
                                    try {
                                        el.dispatchEvent(new PointerEvent('pointerdown', evtInit));
                                        el.dispatchEvent(new MouseEvent('mousedown', evtInit));
                                        el.dispatchEvent(new MouseEvent('mouseup', evtInit));
                                        el.dispatchEvent(new MouseEvent('click', evtInit));
                                    } catch (e) {}
                                }, element.selector);
                                const clickPt = Array.isArray(points) && points.length ? points[0] : null;
                                if (clickPt) {
                                    if (this.mode === 'mobile' && this.page.touchscreen) {
                                        await this.page.touchscreen.tap(clickPt.x, clickPt.y);
                                    } else {
                                        await this.page.mouse.click(clickPt.x, clickPt.y, { delay: 10 });
                                    }
                                }
                                await new Promise(r => setTimeout(r, 100));
                                const curRoute = await this.getVirtualUrl();
                                if (curRoute !== beforeRoute) { success = true; }
                            }
                            if (!success) {
                                // 兜底：直接在浏览器上下文触发点击
                                await this.page.evaluate((cssSelector) => {
                                    const deepQuerySelector = (selector) => {
                                        const tryRoot = (root) => root ? root.querySelector(selector) : null;
                                        let found = tryRoot(document);
                                        if (found) return found;
                                        const stack = [];
                                        const all = document.querySelectorAll('*');
                                        all.forEach(el => { if (el.shadowRoot) stack.push(el.shadowRoot); });
                                        while (stack.length) {
                                            const root = stack.pop();
                                            found = tryRoot(root);
                                            if (found) return found;
                                            root.querySelectorAll('*').forEach(el => { if (el.shadowRoot) stack.push(el.shadowRoot); });
                                        }
                                        return null;
                                    };
                                    const el = deepQuerySelector(cssSelector);
                                    if (!el) return;
                                    const evtInit = { bubbles: true, cancelable: true, view: window };
                                    try {
                                        el.dispatchEvent(new PointerEvent('pointerdown', evtInit));
                                        el.dispatchEvent(new MouseEvent('mousedown', evtInit));
                                        el.dispatchEvent(new MouseEvent('mouseup', evtInit));
                                        el.dispatchEvent(new MouseEvent('click', evtInit));
                                    } catch (e) {}
                                }, element.selector);
                                const clickPt = Array.isArray(points) && points.length ? points[0] : null;
                                if (clickPt) {
                                    if (this.mode === 'mobile' && this.page.touchscreen) {
                                        await this.page.touchscreen.tap(clickPt.x, clickPt.y);
                                    } else {
                                        await this.page.mouse.click(clickPt.x, clickPt.y, { delay: 10 });
                                    }
                                }
                                await new Promise(r => setTimeout(r, 100));
                                const curRoute = await this.getVirtualUrl();
                                if (curRoute !== beforeRoute) { success = true; }
                            }
                            if (!success) {
                                // 兜底：直接在浏览器上下文触发点击
                                await this.page.evaluate((cssSelector) => {
                                    const deepQuerySelector = (selector) => {
                                        const tryRoot = (root) => root ? root.querySelector(selector) : null;
                                        let found = tryRoot(document);
                                        if (found) return found;
                                        const stack = [];
                                        const all = document.querySelectorAll('*');
                                        all.forEach(el => { if (el.shadowRoot) stack.push(el.shadowRoot); });
                                        while (stack.length) {
                                            const root = stack.pop();
                                            found = tryRoot(root);
                                            if (found) return found;
                                            root.querySelectorAll('*').forEach(el => { if (el.shadowRoot) stack.push(el.shadowRoot); });
                                        }
                                        return null;
                                    };
                                    const el = deepQuerySelector(cssSelector);
                                    if (!el) return;
                                    const evtInit = { bubbles: true, cancelable: true, view: window };
                                    try {
                                        el.dispatchEvent(new PointerEvent('pointerdown', evtInit));
                                        el.dispatchEvent(new MouseEvent('mousedown', evtInit));
                                        el.dispatchEvent(new MouseEvent('mouseup', evtInit));
                                        el.dispatchEvent(new MouseEvent('click', evtInit));
                                    } catch (e) {}
                                }, element.selector);
                                const clickPt = Array.isArray(points) && points.length ? points[0] : null;
                                if (clickPt) {
                                    if (this.mode === 'mobile' && this.page.touchscreen) {
                                        await this.page.touchscreen.tap(clickPt.x, clickPt.y);
                                    } else {
                                        await this.page.mouse.click(clickPt.x, clickPt.y, { delay: 10 });
                                    }
                                }
                                await new Promise(r => setTimeout(r, 100));
                                const curRoute = await this.getVirtualUrl();
                                if (curRoute !== beforeRoute) { success = true; }
                            }
                        } else {
                            // 兜底：直接在浏览器上下文触发点击
                            await this.page.evaluate((cssSelector) => {
                                const deepQuerySelector = (selector) => {
                                    const tryRoot = (root) => root ? root.querySelector(selector) : null;
                                    let found = tryRoot(document);
                                    if (found) return found;
                                    const stack = [];
                                    const all = document.querySelectorAll('*');
                                    all.forEach(el => { if (el.shadowRoot) stack.push(el.shadowRoot); });
                                    while (stack.length) {
                                        const root = stack.pop();
                                        found = tryRoot(root);
                                        if (found) return found;
                                        root.querySelectorAll('*').forEach(el => { if (el.shadowRoot) stack.push(el.shadowRoot); });
                                    }
                                    return null;
                                };
                                const el = deepQuerySelector(cssSelector);
                                if (!el) return false;
                                try { el.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
                                const rect = el.getBoundingClientRect();
                                const cx = Math.floor(rect.left + rect.width / 2);
                                const cy = Math.floor(rect.top + rect.height / 2);
                                const evtInit = { bubbles: true, cancelable: true, view: window, clientX: cx, clientY: cy, button: 0, buttons: 1 };
                                try {
                                    el.dispatchEvent(new TouchEvent('touchstart', evtInit));
                                    el.dispatchEvent(new PointerEvent('pointerdown', evtInit));
                                    el.dispatchEvent(new MouseEvent('mousedown', evtInit));
                                    el.dispatchEvent(new TouchEvent('touchend', evtInit));
                                    el.dispatchEvent(new MouseEvent('mouseup', evtInit));
                                    el.dispatchEvent(new MouseEvent('click', evtInit));
                                } catch (e) {
                                    el.dispatchEvent(new Event('touchstart', evtInit));
                                    el.dispatchEvent(new Event('touchend', evtInit));
                                    el.dispatchEvent(new Event('click', evtInit));
                                }
                                if (typeof el.click === 'function') el.click();
                                return true;
                            }, element.selector);
                            // iframe/frame 兜底：在各 frame 中尝试点击
                            let frameClicked = false;
                            for (const frame of this.page.frames()) {
                                try {
                                    const handle = await frame.$(element.selector);
                                    if (!handle) continue;
                                    const box = await handle.boundingBox();
                                    if (!box) continue;
                                    const cx = Math.floor(box.x + box.width / 2);
                                    const cy = Math.floor(box.y + box.height / 2);
                                    await frame.evaluate((el, coords) => {
                                        try { el.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
                                        const init = { bubbles: true, cancelable: true, view: window, clientX: coords.x, clientY: coords.y, button: 0, buttons: 1 };
                                        el.dispatchEvent(new PointerEvent('pointerenter', init));
                                        el.dispatchEvent(new MouseEvent('mouseover', init));
                                        el.dispatchEvent(new MouseEvent('mousemove', init));
                                        el.dispatchEvent(new PointerEvent('pointerdown', init));
                                        el.dispatchEvent(new MouseEvent('mousedown', init));
                                        el.dispatchEvent(new MouseEvent('mouseup', init));
                                        el.dispatchEvent(new MouseEvent('click', init));
                                    }, handle, { x: cx, y: cy });
                                    if (this.mode === 'mobile' && this.page.touchscreen) {
                                        await this.page.touchscreen.tap(cx, cy);
                                    } else {
                                        await this.page.mouse.click(cx, cy, { delay: 10 });
                                    }
                                    await new Promise(r => setTimeout(r, 100));
                                    const curRoute = await this.getVirtualUrl();
                                    if (curRoute !== beforeRoute) { frameClicked = true; }
                                } catch (_) {}
                            }
                            if (!frameClicked) {
                                // 最后再尝试一次元素句柄点击（如果在主 frame 可找到）
                                try {
                                    const h = await this.page.$(element.selector);
                                    if (h) {
                                        await h.evaluate(el => { try { el.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {} });
                                        await h.click({ delay: 10 });
                                        await new Promise(r => setTimeout(r, 100));
                                    }
                                } catch (_) {}
                            }
                        }
                    } else {
                        console.log(`[SPANavigator] 直接使用CSS选择器进行点击(含祖先提升): ${element.selector}`);
                        const points = await this.page.evaluate((cssSelector) => {
                            function isVisible(el) {
                                if (!el) return false;
                                const style = window.getComputedStyle(el);
                                const rect = el.getBoundingClientRect();
                                return rect.width > 0 && rect.height > 0 &&
                                       style.visibility !== 'hidden' &&
                                       style.display !== 'none' &&
                                       style.opacity !== '0';
                            }
                            function isInteractive(el) {
                                if (!el) return false;
                                const tag = (el.tagName || '').toLowerCase();
                                const style = window.getComputedStyle(el);
                                if (style.pointerEvents === 'none') return false;
                                if (tag === 'a' && el.hasAttribute('href')) return true;
                                if (tag === 'button') return true;
                                if (tag === 'input') {
                                    const type = (el.getAttribute('type') || '').toLowerCase();
                                    if (['button','submit','checkbox','radio'].includes(type)) return true;
                                }
                                const role = el.getAttribute('role');
                                if (role === 'button' || role === 'link' || role === 'tab') return true;
                                const tabindex = parseInt(el.getAttribute('tabindex') || '-1', 10);
                                if (!Number.isNaN(tabindex) && tabindex >= 0) return true;
                                if (typeof el.onclick === 'function') return true;
                                if (style.cursor === 'pointer') return true;
                                return false;
                            }
                            function isDecoration(el) {
                                if (!el) return false;
                                const tag = (el.tagName || '').toLowerCase();
                                if (tag === 'i' || tag === 'svg') return true;
                                const cls = (el.className || '').toString().toLowerCase();
                                if (cls.includes('icon') || cls.includes('badge') || cls.includes('arrow')) return true;
                                const rect = el.getBoundingClientRect();
                                const textLen = (el.textContent || '').trim().length;
                                if (textLen === 0 && rect.width <= 24 && rect.height <= 24) return true;
                                return false;
                            }
                            function pickTarget(startEl) {
                                let cur = startEl;
                                const maxDepth = 8;
                                const viewportArea = window.innerWidth * window.innerHeight;
                                const startRect = startEl.getBoundingClientRect();
                                for (let d = 0; d < maxDepth && cur; d++) {
                                    if (!isVisible(cur)) { cur = cur.parentElement; continue; }
                                    const rect = cur.getBoundingClientRect();
                                    const area = rect.width * rect.height;
                                    const heightOk = rect.height >= 28 && rect.height <= 140;
                                    const widthOk = rect.width >= Math.min(window.innerWidth * 0.5, 240);
                                    const grows = rect.width >= startRect.width * 1.5 || rect.height >= startRect.height * 1.5;
                                    if (isInteractive(cur) && !isDecoration(cur)) return cur;
                                    if (!isDecoration(cur) && heightOk && widthOk && grows && area < viewportArea * 0.85) {
                                        return cur;
                                    }
                                    const descendant = cur.querySelector('a[href], button, input[type="button"], input[type="submit"], [role="button"], [tabindex]');
                                    if (descendant && isVisible(descendant)) return descendant;
                                    const style = window.getComputedStyle(cur);
                                    if (style.pointerEvents === 'none') { cur = cur.parentElement; continue; }
                                    cur = cur.parentElement;
                                }
                                return startEl;
                            }
                            const start = document.querySelector(cssSelector);
                            if (!start) return null;
                            try { start.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
                            const target = pickTarget(start);
                            if (!target) return null;
                            const rect = target.getBoundingClientRect();
                            const cx = Math.floor(rect.left + rect.width / 2);
                            const cy = Math.floor(rect.top + rect.height / 2);
                            const leftMid = { x: Math.floor(rect.left + rect.width * 0.2), y: cy };
                            const rightMid = { x: Math.floor(rect.left + rect.width * 0.8), y: cy };
                            const topMid = { x: cx, y: Math.floor(rect.top + rect.height * 0.3) };
                            const bottomMid = { x: cx, y: Math.floor(rect.top + rect.height * 0.7) };
                            const center = { x: cx, y: cy };
                            return [center, leftMid, rightMid, topMid, bottomMid];
                        }, element.selector);
                        if (Array.isArray(points) && points.length) {
                            let success = false;
                            for (const pt of points) {
                                // 在坐标点击前，对选中的目标元素分派鼠标事件以提高兼容性
                                await this.page.evaluate((cssSelector) => {
                                    function isVisible(el) {
                                        if (!el) return false;
                                        const style = window.getComputedStyle(el);
                                        const rect = el.getBoundingClientRect();
                                        return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
                                    }
                                    function isInteractive(el) {
                                        if (!el) return false;
                                        const tag = (el.tagName || '').toLowerCase();
                                        const style = window.getComputedStyle(el);
                                        if (style.pointerEvents === 'none') return false;
                                        if (tag === 'a' && el.hasAttribute('href')) return true;
                                        if (tag === 'button') return true;
                                        const role = el.getAttribute('role');
                                        if (role === 'button' || role === 'link' || role === 'tab') return true;
                                        return false;
                                    }
                                    function pickTarget(startEl) {
                                        let cur = startEl;
                                        const maxDepth = 8;
                                        for (let d = 0; d < maxDepth && cur; d++) {
                                            if (!isVisible(cur)) { cur = cur.parentElement; continue; }
                                            if (isInteractive(cur)) return cur;
                                            const descendant = cur.querySelector('a[href], button, [role="button"], [tabindex]');
                                            if (descendant && isVisible(descendant)) return descendant;
                                            cur = cur.parentElement;
                                        }
                                        return startEl;
                                    }
                                    const start = document.querySelector(cssSelector);
                                    if (!start) return;
                                    const target = pickTarget(start);
                                    if (!target) return;
                                    const evtInit = { bubbles: true, cancelable: true, view: window };
                                    try {
                                        target.dispatchEvent(new PointerEvent('pointerdown', evtInit));
                                        target.dispatchEvent(new MouseEvent('mousedown', evtInit));
                                        target.dispatchEvent(new MouseEvent('mouseup', evtInit));
                                        target.dispatchEvent(new MouseEvent('click', evtInit));
                                    } catch (e) {}
                                }, element.selector);
                                const clickPt = Array.isArray(points) && points.length ? points[0] : null;
                                if (clickPt) {
                                    if (this.mode === 'mobile' && this.page.touchscreen) {
                                        await this.page.touchscreen.tap(clickPt.x, clickPt.y);
                                    } else {
                                        await this.page.mouse.click(clickPt.x, clickPt.y, { delay: 10 });
                                    }
                                }
                                await new Promise(r => setTimeout(r, 100));
                                const curRoute = await this.getVirtualUrl();
                                if (curRoute !== beforeRoute) { success = true; }
                            }
                            if (!success) {
                                // 兜底：直接在浏览器上下文触发点击
                                await this.page.evaluate((cssSelector) => {
                                    const el = document.querySelector(cssSelector);
                                    if (!el) return false;
                                    try { el.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
                                    const evtInit = { bubbles: true, cancelable: true, view: window };
                                    try {
                                        el.dispatchEvent(new PointerEvent('pointerdown', evtInit));
                                        el.dispatchEvent(new MouseEvent('mousedown', evtInit));
                                        el.dispatchEvent(new MouseEvent('mouseup', evtInit));
                                        el.dispatchEvent(new MouseEvent('click', evtInit));
                                    } catch (e) {}
                                    if (typeof el.click === 'function') el.click();
                                    return true;
                                }, element.selector);
                            }
                        } else {
                            // 兜底：直接在浏览器上下文触发点击
                            await this.page.evaluate((cssSelector) => {
                                const el = document.querySelector(cssSelector);
                                if (!el) return false;
                                try { el.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
                                const evtInit = { bubbles: true, cancelable: true, view: window };
                                try {
                                    el.dispatchEvent(new PointerEvent('pointerdown', evtInit));
                                    el.dispatchEvent(new MouseEvent('mousedown', evtInit));
                                    el.dispatchEvent(new MouseEvent('mouseup', evtInit));
                                    el.dispatchEvent(new MouseEvent('click', evtInit));
                                } catch (e) {}
                                if (typeof el.click === 'function') el.click();
                                return true;
                            }, element.selector);
                        }
                    }
                }
            } else {
                // 如果element是puppeteer的ElementHandle
                console.log(`[SPANavigator] 直接点击元素...`);
                if (this.mode === 'mobile' && this.page.touchscreen && element && typeof element.boundingBox === 'function') {
                    const box = await element.boundingBox();
                    if (box) {
                        await this.page.touchscreen.tap(Math.floor(box.x + box.width / 2), Math.floor(box.y + box.height / 2));
                    } else {
                        await element.click();
                    }
                } else {
                    // 桌面优先使用坐标点击
                    if (element && typeof element.boundingBox === 'function') {
                        const box = await element.boundingBox();
                        if (box) {
                            await this.page.mouse.click(Math.floor(box.x + box.width / 2), Math.floor(box.y + box.height / 2), { delay: 10 });
                        } else {
                            await element.click();
                        }
                    } else {
                        await element.click();
                    }
                }
            }
        } catch (error) {
            console.warn(`[SPANavigator] 点击元素时出错: ${error.message}`);
            
            // 尝试备用点击策略
            try {
                console.log(`[SPANavigator] 尝试备用点击策略...`);
                if (element && element.selector && !element.selector.trim().startsWith('<')) {
                    if (this.mode === 'mobile' && this.page.touchscreen) {
                        const coords = await this.page.evaluate((cssSelector) => {
                            // 1. 直接查找 + 祖先提升兜底
                            function isVisible(el) {
                                if (!el) return false;
                                const style = window.getComputedStyle(el);
                                const rect = el.getBoundingClientRect();
                                return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
                            }
                            function isInteractive(el) {
                                if (!el) return false;
                                const tag = (el.tagName || '').toLowerCase();
                                const style = window.getComputedStyle(el);
                                if (style.pointerEvents === 'none') return false;
                                if (tag === 'a' && el.hasAttribute('href')) return true;
                                if (tag === 'button') return true;
                                if (tag === 'input') {
                                    const type = (el.getAttribute('type') || '').toLowerCase();
                                    if (['button','submit','checkbox','radio'].includes(type)) return true;
                                }
                                const role = el.getAttribute('role');
                                if (role === 'button' || role === 'link' || role === 'tab') return true;
                                const tabindex = parseInt(el.getAttribute('tabindex') || '-1', 10);
                                if (!Number.isNaN(tabindex) && tabindex >= 0) return true;
                                if (typeof el.onclick === 'function') return true;
                                if (style.cursor === 'pointer') return true;
                                return false;
                            }
                            function pickTarget(el) {
                                let cur = el;
                                const maxDepth = 8;
                                for (let d = 0; d < maxDepth && cur; d++) {
                                    if (!isVisible(cur)) { cur = cur.parentElement; continue; }
                                    if (isInteractive(cur)) return cur;
                                    const descendant = cur.querySelector('a[href], button, input[type="button"], input[type="submit"], [role="button"], [tabindex]');
                                    if (descendant && isVisible(descendant)) return descendant;
                                    cur = cur.parentElement;
                                }
                                return el;
                            }
                            let targetElement = document.querySelector(cssSelector);
                            if (!targetElement && cssSelector.includes(':nth-child(')) {
                                const simplified = cssSelector.replace(/:nth-child\(\d+\)/g, '');
                                const els = document.querySelectorAll(simplified);
                                if (els.length > 0) targetElement = els[0];
                            }
                            if (!targetElement && cssSelector.includes(' > ')) {
                                const parts = cssSelector.split(' > ');
                                for (let i = parts.length - 1; i >= 0; i--) {
                                    const partial = parts.slice(i).join(' > ');
                                    const els = document.querySelectorAll(partial);
                                    if (els.length > 0) { targetElement = els[0]; break; }
                                }
                            }
                            if (!targetElement) return null;
                            targetElement = pickTarget(targetElement);
                            try { targetElement.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
                            const rect = targetElement.getBoundingClientRect();
                            return { x: Math.floor(rect.left + rect.width / 2), y: Math.floor(rect.top + rect.height / 2) };
                        }, element.selector);
                        if (coords) {
                            await this.page.touchscreen.tap(coords.x, coords.y);
                        } else {
                            throw new Error('移动端备用策略未找到可点击坐标');
                        }
                    } else {
                        const clicked = await this.page.evaluate((cssSelector) => {
                            // 尝试多种查找策略 + 祖先提升
                            function isVisible(el) {
                                if (!el) return false;
                                const style = window.getComputedStyle(el);
                                const rect = el.getBoundingClientRect();
                                return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
                            }
                            function isInteractive(el) {
                                if (!el) return false;
                                const tag = (el.tagName || '').toLowerCase();
                                const style = window.getComputedStyle(el);
                                if (style.pointerEvents === 'none') return false;
                                if (tag === 'a' && el.hasAttribute('href')) return true;
                                if (tag === 'button') return true;
                                if (tag === 'input') {
                                    const type = (el.getAttribute('type') || '').toLowerCase();
                                    if (['button','submit','checkbox','radio'].includes(type)) return true;
                                }
                                const role = el.getAttribute('role');
                                if (role === 'button' || role === 'link' || role === 'tab') return true;
                                const tabindex = parseInt(el.getAttribute('tabindex') || '-1', 10);
                                if (!Number.isNaN(tabindex) && tabindex >= 0) return true;
                                if (typeof el.onclick === 'function') return true;
                                if (style.cursor === 'pointer') return true;
                                return false;
                            }
                            function pickTarget(el) {
                                let cur = el;
                                const maxDepth = 8;
                                for (let d = 0; d < maxDepth && cur; d++) {
                                    if (!isVisible(cur)) { cur = cur.parentElement; continue; }
                                    if (isInteractive(cur)) return cur;
                                    const descendant = cur.querySelector('a[href], button, input[type="button"], input[type="submit"], [role="button"], [tabindex]');
                                    if (descendant && isVisible(descendant)) return descendant;
                                    cur = cur.parentElement;
                                }
                                return el;
                            }
                            let targetElement = null;
                            // 1. 直接查找
                            targetElement = document.querySelector(cssSelector);
                            // 2. 如果包含nth-child，尝试移除nth-child再查找
                            if (!targetElement && cssSelector.includes(':nth-child(')) {
                                const simplifiedSelector = cssSelector.replace(/:nth-child\(\d+\)/g, '');
                                const elements = document.querySelectorAll(simplifiedSelector);
                                if (elements.length > 0) targetElement = elements[0];
                            }
                            // 3. 如果是复合选择器，尝试逐级简化
                            if (!targetElement && cssSelector.includes(' > ')) {
                                const parts = cssSelector.split(' > ');
                                for (let i = parts.length - 1; i >= 0; i--) {
                                    const partialSelector = parts.slice(i).join(' > ');
                                    const elements = document.querySelectorAll(partialSelector);
                                    if (elements.length > 0) { targetElement = elements[0]; break; }
                                }
                            }
                            if (!targetElement) return false;
                            targetElement = pickTarget(targetElement);
                            try { targetElement.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
                            if (typeof targetElement.click === 'function') {
                                targetElement.click();
                                return true;
                            }
                            return false;
                        }, element.selector);
                        if (!clicked) {
                            throw new Error('所有备用策略都失败了');
                        }
                    }
                }
            } catch (backupError) {
                console.warn(`[SPANavigator] 备用点击也失败: ${backupError.message}`);
            }
        }
        
        // 等待可能的路由变化
        console.log(`[SPANavigator] 等待可能的路由变化...`);
        await new Promise(resolve => setTimeout(resolve, 400));
        
        // 检查是否出现了弹窗
        console.log(`[SPANavigator] 检查是否出现弹窗...`);
        const hasPopup = await this.detectPopup();
        console.log(`[SPANavigator] 检测到弹窗: ${hasPopup}`);
        
        const afterRoute = await this.getVirtualUrl();
        console.log(`[SPANavigator] 点击后路由: ${afterRoute}`);
        const routeChanged = beforeRoute !== afterRoute;
        console.log(`[SPANavigator] 路由是否变化: ${routeChanged}`);
        
        // 检查页面URL是否有实际变化
        const beforeUrl = await this.page.url();
        await new Promise(resolve => setTimeout(resolve, 300));
        const afterUrl = await this.page.url();
        const urlChanged = beforeUrl !== afterUrl;
        
        if (urlChanged) {
            console.log(`[SPANavigator] 检测到URL实际变化: ${beforeUrl} -> ${afterUrl}`);
        }
        
        // 新标签页检测与处理
        let newTabOpened = false;
        let newTabUrl = null;
        let newTabTitle = null;
        let newTabClosed = false;
        try {
            let newPage = null;
            if (_popupPage) {
                newPage = _popupPage;
            } else if (_targetCreated) {
                try { newPage = await _targetCreated.page(); } catch (e) {}
            }
            if (newPage && newPage !== this.page) {
                newTabOpened = true;
                try {
                    await newPage.bringToFront().catch(() => {});
                    try {
                        await newPage.waitForNavigation({ waitUntil: ['domcontentloaded', 'networkidle2'], timeout: 5000 }).catch(() => {});
                    } catch (e) {}
                    newTabUrl = await newPage.url();
                    try { newTabTitle = await newPage.title(); } catch (e) {}
                    await newPage.close({ runBeforeUnload: false }).catch(() => {});
                    newTabClosed = true;
                    console.log(`[SPANavigator] 检测到新标签页并已关闭: ${newTabUrl}`);
                } catch (e) {
                    console.warn(`[SPANavigator] 处理新标签页时出错: ${e.message}`);
                }
            }
        } catch (e) {
            console.warn(`[SPANavigator] 新标签页检测逻辑异常: ${e.message}`);
        } finally {
            try { if (browser && typeof browser.removeListener === 'function') browser.removeListener('targetcreated', _targetCreatedHandler); } catch (e) {}
            try { if (this.page && typeof this.page.removeListener === 'function') this.page.removeListener('popup', _popupHandler); } catch (e) {}
        }
        
        // 如果检测到弹窗，尝试获取弹窗信息
        let popupInfo = null;
        if (hasPopup) {
            popupInfo = await this.getPopupInfo();
            console.log(`[SPANavigator] 弹窗信息: `, popupInfo);
        }
        
        return {
            success: true,
            routeChanged,
            urlChanged,
            beforeUrl: beforeRoute,
            newUrl: afterRoute,
            realUrl: afterUrl,
            hasPopup,
            popupInfo,
            newTabOpened,
            newTabUrl,
            newTabTitle,
            newTabClosed
        };
    }

    // 处理HTML选择器的旧方法（向后兼容）
    async handleHtmlSelector(selectorHtml) {
        console.log(`[SPANavigator] 使用HTML选择器处理方式... (mode=${this.mode})`);
        
        // 兜底去噪：跳过资源/元信息标签点击
        const lower = (selectorHtml || '').toLowerCase();
        if (/^\s*<\s*(link|script|meta|base|style)\b/.test(lower) || /\brel\s*=\s*["']stylesheet["']/.test(lower)) {
            console.log('[SPANavigator] 非交互资源/元信息标签（link/script/meta/base/style），跳过点击');
            return;
        }
        
        if (this.mode === 'mobile' && this.page.touchscreen) {
            const coords = await this.page.evaluate((selectorHtml) => {
                const tempDiv = document.createElement('div');
                tempDiv.innerHTML = selectorHtml;
                const tempEl = tempDiv.firstChild;
                if (!tempEl || tempEl.nodeType !== Node.ELEMENT_NODE) return null;
                const isMenuItem = /menu-item|ant-menu-item|li.*role=\"menuitem\"/i.test(selectorHtml || '');
                const isDropdownTrigger = /dropdown-trigger|ant-dropdown-trigger|aria-haspopup=\"true\"/i.test(selectorHtml || '');
                if (isMenuItem || isDropdownTrigger) {
                    const menuText = tempEl.textContent ? tempEl.textContent.trim() : '';
                    if (menuText) {
                        const nodes = document.querySelectorAll(isMenuItem ? 'li.ant-menu-item, [role=\"menuitem\"], .menu-item' : '.dropdown-trigger, [aria-haspopup=\"true\"], .ant-dropdown-trigger');
                        for (const item of nodes) {
                            if (item.textContent && item.textContent.trim() === menuText) {
                                try { item.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
                                const r = item.getBoundingClientRect();
                                return { x: Math.floor(r.left + r.width / 2), y: Math.floor(r.top + r.height / 2) };
                            }
                        }
                    }
                }
                if (tempEl.tagName) {
                    const matched = document.querySelectorAll(tempEl.tagName);
                    for (const el of matched) {
                        if (el.outerHTML === selectorHtml) {
                            try { el.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
                            const rect = el.getBoundingClientRect();
                            return { x: Math.floor(rect.left + rect.width / 2), y: Math.floor(rect.top + rect.height / 2) };
                        }
                    }
                }
                return null;
            }, selectorHtml);
            if (coords) {
                await this.page.touchscreen.tap(coords.x, coords.y);
                return;
            }
            await this.page.evaluate((selectorHtml) => {
                const tempDiv = document.createElement('div');
                tempDiv.innerHTML = selectorHtml;
                const tempEl = tempDiv.firstChild;
                if (!tempEl) return false;
                const matchedElements = document.querySelectorAll(tempEl.tagName);
                for (const el of matchedElements) {
                    if (el.outerHTML === selectorHtml && typeof el.click === 'function') {
                        el.click();
                        return true;
                    }
                }
                return false;
            }, selectorHtml);
            return;
        }
    }
    
    async detectPopup() {
        // 检测页面中是否存在弹窗(modal, dialog, alert等)，增加导航上下文销毁的重试容错
        try {
            console.log(`[SPANavigator] 开始检测弹窗...`);
            const maxRetries = 3;
            for (let attempt = 0; attempt < maxRetries; attempt++) {
                try {
                    const hasPopup = await this.page.evaluate(() => {
                        try {
                            // 尝试捕获浏览器是否存在对话框
                            if (window.alert?._orig || window.confirm?._orig || window.prompt?._orig) {
                                console.log('检测到可能的原生对话框拦截');
                                return true;
                            }
                        } catch (e) {} // 忽略错误
                        
                        // 检测各种可能的弹窗元素
                        const popupSelectors = [
                            // 常见弹窗选择器
                            '.modal', '.dialog', '.popup', '[role="dialog"]', '[aria-modal="true"]',
                            // Bootstrap弹窗
                            '.modal.show', '.modal-dialog', '.modal-content',
                            // 其他常见框架的弹窗
                            '.ant-modal', '.el-dialog', '.v-dialog', '.MuiDialog-root', '.ReactModal__Content',
                            '.ui-dialog', '.ui-modal', '.a-modal', '.modal-open .modal',
                            // 移动框架弹窗
                            '.weui-dialog', '.van-dialog', '.mint-popup', '.am-modal',
                            // 通用样式特征
                            'div[style*="z-index"][style*="position: fixed"]',
                            'div[style*="z-index: 10"][style*="position: absolute"]',
                            'div.overlay', '.toast', '.notification', '.toast-container .toast-message', 
                            '.notification-content', '.tip', '.tips', '.popover-content',
                            // 成功/错误信息框
                            '.alert', '.message-box', '.success-message', '.error-message', 
                            '.success-box', '.error-box', '.info-box', '.warning-box',
                            '[class*="message"][class*="success"]', '[class*="message"][class*="error"]',
                            // 常见组件库的消息框
                            '.ant-message', '.el-message', '.el-message-box', '.ant-notification',
                            '.toast-success', '.toast-error', '.toast-info', '.toast-warning',
                            // 特定框架的弹窗/提示
                            '[class*="popup"]', '[class*="modal"]', '[class*="dialog"]', 
                            '[class*="alert"]', '[class*="toast"]', '[class*="notification"]'
                        ];
                        
                        // 通用检测逻辑 - 基于选择器
                        for (const selector of popupSelectors) {
                            try {
                                const elements = document.querySelectorAll(selector);
                                for (const el of elements) {
                                    // 检查元素是否可见
                                    const style = window.getComputedStyle(el);
                                    if (el.offsetWidth > 10 && // 忽略太小的元素
                                        el.offsetHeight > 10 && 
                                        style.visibility !== 'hidden' && 
                                        style.display !== 'none' &&
                                        style.opacity !== '0') {
                                        // 检查内容是否有意义
                                        if (el.innerText && el.innerText.trim().length > 0) {
                                            return true;
                                        }
                                    }
                                }
                            } catch (err) {
                                // 忽略单个选择器的错误
                            }
                        }
                        
                        // 检测遮罩层/模态效果
                        try {
                            const overlaySelectors = [
                                '.overlay', '.mask', '.backdrop', '.modal-backdrop',
                                '[style*="background-color: rgba"][style*="position: fixed"]',
                                '[class*="overlay"]', '[class*="mask"]', '[class*="backdrop"]',
                                'div[style*="opacity"][style*="background"][style*="fixed"]'
                            ];
                            for (const selector of overlaySelectors) {
                                const overlays = document.querySelectorAll(selector);
                                for (const overlay of overlays) {
                                    const style = window.getComputedStyle(overlay);
                                    if (style.display !== 'none' && style.visibility !== 'hidden' && parseFloat(style.opacity || '1') > 0.1) {
                                        return true;
                                    }
                                }
                            }
                        } catch (e) {}
                        
                        // 检测文档主体的变化，可能表示有模态框/弹窗
                        try {
                            if (document.body.style.overflow === 'hidden' ||
                                document.body.style.position === 'fixed' || 
                                document.documentElement.style.overflow === 'hidden') {
                                return true;
                            }
                        } catch (e) {}
                        
                        return false;
                    });
                    console.log(`[SPANavigator] 弹窗检测结果: ${hasPopup}`);
                    return hasPopup;
                } catch (error) {
                    const isContextDestroyed = !!(error && error.message && error.message.includes('Execution context was destroyed'));
                    console.warn(`[SPANavigator] 检测弹窗时出错: ${error.message}`);
                    if (isContextDestroyed) {
                        // 等待导航稳定后重试
                        try { await this.page.waitForNavigation({ waitUntil: ['domcontentloaded', 'networkidle2'], timeout: 2000 }); } catch (e) {}
                        await new Promise(r => setTimeout(r, 200));
                        continue;
                    } else {
                        return false;
                    }
                }
            }
            return false;
        } catch (error) {
            console.warn(`[SPANavigator] 检测弹窗时出错（外层）: ${error.message}`);
            return false;
        }
    }
    
    async getPopupInfo() {
        // 获取弹窗的详细信息
        try {
            console.log(`[SPANavigator] 获取弹窗信息...`);
            const popupInfo = await this.page.evaluate(() => {
                // 寻找弹窗元素
                const popupSelectors = [
                    '.modal', '.dialog', '.popup', '[role="dialog"]', '[aria-modal="true"]',
                    '.modal.show', '.modal-dialog', '.modal-content',
                    '.ant-modal', '.el-dialog', '.v-dialog',
                    'div[style*="z-index"][style*="position: fixed"]',
                    'div[style*="z-index: 1"][style*="position: absolute"]',
                    '.overlay', '.toast', '.notification',
                    '.alert', '.message-box', '.success-message', '.error-message'
                ];
                
                let popupElement = null;
                
                // 找出第一个可见的弹窗元素
                for (const selector of popupSelectors) {
                    const elements = document.querySelectorAll(selector);
                    for (const el of elements) {
                        const style = window.getComputedStyle(el);
                        if (el.offsetWidth && 
                            el.offsetHeight && 
                            style.visibility !== 'hidden' && 
                            style.display !== 'none') {
                            popupElement = el;
                            break;
                        }
                    }
                    if (popupElement) break;
                }
                
                if (!popupElement) return null;
                
                // 提取弹窗信息
                const info = {
                    type: popupElement.tagName.toLowerCase(),
                    className: popupElement.className,
                    id: popupElement.id,
                    text: popupElement.innerText || popupElement.textContent,
                    htmlContent: popupElement.innerHTML,
                    hasCloseButton: !!popupElement.querySelector('button.close, .btn-close, [aria-label="Close"], .closebtn'),
                    hasConfirmButton: !!popupElement.querySelector('button[type="submit"], button.submit, button.confirm, .btn-primary, .confirm-btn'),
                    hasCancelButton: !!popupElement.querySelector('button[type="reset"], button.cancel, .btn-secondary, .cancel-btn'),
                };
                
                // 识别弹窗类型
                if (info.text.toLowerCase().includes('success') || 
                    info.className.toLowerCase().includes('success') ||
                    popupElement.querySelector('.success-icon, .icon-success')) {
                    info.messageType = 'success';
                } else if (info.text.toLowerCase().includes('error') || 
                           info.className.toLowerCase().includes('error') ||
                           popupElement.querySelector('.error-icon, .icon-error')) {
                    info.messageType = 'error';
                } else if (info.text.toLowerCase().includes('warning') || 
                           info.className.toLowerCase().includes('warning') ||
                           popupElement.querySelector('.warning-icon, .icon-warning')) {
                    info.messageType = 'warning';
                } else if (info.text.toLowerCase().includes('info') || 
                           info.className.toLowerCase().includes('info') ||
                           popupElement.querySelector('.info-icon, .icon-info')) {
                    info.messageType = 'info';
                } else {
                    info.messageType = 'unknown';
                }
                
                return info;
            });
            
            console.log(`[SPANavigator] 获取到弹窗信息`);
            return popupInfo;
        } catch (error) {
            console.warn(`[SPANavigator] 获取弹窗信息时出错: ${error.message}`);
            return null;
        }
    }

    async detectRouteChange() {
        // 检查路由是否发生变化
        const before = await this.getVirtualUrl();
        // 使用setTimeout和Promise替换waitForTimeout
        await new Promise(resolve => setTimeout(resolve, 500));
        const after = await this.getVirtualUrl();
        return before !== after;
    }

    async getVirtualUrl() {
        // 获取SPA虚拟路径
        return await this.page.evaluate(() => {
            try {
                // 检测各种SPA框架路由信息
                
                // React Router (v5/v6)
                if (window.__REACT_ROUTER_GLOBAL_HISTORY__) {
                    return window.location.origin + window.__REACT_ROUTER_GLOBAL_HISTORY__.location.pathname;
                }
                
                // Next.js
                if (window.__NEXT_DATA__ && window.__NEXT_DATA__.page) {
                    return window.location.origin + window.__NEXT_DATA__.page;
                }
                
                // Vue Router
                if (window.$nuxt && window.$nuxt.$route) {
                    return window.location.origin + window.$nuxt.$route.fullPath;
                }
                
                // Angular Router
                const angularRoot = document.querySelector('[ng-version]');
                if (angularRoot && angularRoot.getAttribute('ng-version')) {
                    const baseElm = document.querySelector('base');
                    const basePath = baseElm ? baseElm.getAttribute('href') : '/';
                    return window.location.origin + basePath + window.location.pathname;
                }
                
                // 检查基于hash的路由
                if (window.location.hash && window.location.hash.length > 1) {
                    return window.location.origin + window.location.pathname + window.location.hash;
                }
                
                // Next.js 路由状态
                if (window.history && window.history.state && window.history.state.as) {
                    return window.location.origin + window.history.state.as;
                }
                
                // 检查页面中的路由标记 (常见于自定义SPA)
                const routeMarker = document.querySelector('[data-route], [data-current-route], .current-route, #current-route');
                if (routeMarker) {
                    const routeValue = routeMarker.getAttribute('data-route') || 
                                      routeMarker.getAttribute('data-current-route') ||
                                      routeMarker.textContent.trim();
                    if (routeValue) {
                        return window.location.origin + routeValue;
                    }
                }
                
                // 无法检测到SPA路由，使用普通URL
                return window.location.href;
            } catch (error) {
                console.error("获取虚拟URL时出错:", error);
                return window.location.href;
            }
        });
    }

    async navigateWithRouteInfo(element, routeInfo) {
        // 可根据routeInfo进行导航，简单实现为点击元素
        if (this.mode === 'mobile' && this.page.touchscreen && element && typeof element.boundingBox === 'function') {
            const box = await element.boundingBox();
            if (box) {
                await this.page.touchscreen.tap(Math.floor(box.x + box.width / 2), Math.floor(box.y + box.height / 2));
            } else {
                await element.click();
            }
        } else {
            await element.click();
        }
        // 使用setTimeout和Promise替换waitForTimeout
        await new Promise(resolve => setTimeout(resolve, 500));
    }
}

module.exports = SPANavigator;
