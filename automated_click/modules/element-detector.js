// 元素检测器
class ElementDetector {
    constructor(config = {}) {
        this.config = config;
        // 可扩展：加载静态规则、LLM配置等
    }

    /**
     * 应用静态规则检测页面可交互元素
     * @param {PageWrapper} pageWrapper
     * @param {string} htmlContent
     * @returns {Promise<Array>} 元素数组
     */
    async detectStaticElements(pageWrapper) {
        // 直接在页面上下文中用静态规则查找可交互元素
        if (!pageWrapper.page) {
            await pageWrapper.init();
        }
        return await pageWrapper.page.evaluate(() => {
            const selectors = [
                // 基本可交互元素
                'a[href]', 'button', 'input[type="button"]', 'input[type="submit"]',
                '[role="button"]', '[onclick]', '[tabindex]',
                
                // 扩展的导航和菜单元素
                '[role="menuitem"]', '[role="menu"] li', '[role="tab"]', 
                '.ant-menu-item', '.dropdown-trigger', '.nav-item', '.menu-item',
                '[aria-haspopup="true"]', '.ant-dropdown-trigger', 
                
                // 可交互的列表项
                'li[tabindex]', 'li[data-menu-id]', 'li.ant-menu-item',
                'li.nav-item', 'li.item', 'li[role]',
                
                // 分页按钮
                '[class*="pag"]', '[class*="next"]', '[class*="prev"]',
                '[class*="page"]', '.pagination a', '.pagination button',
                '[aria-label*="page"]', '[aria-label*="下一页"]', '[aria-label*="上一页"]',
                
                // 加载更多
                '[class*="load-more"]', '[class*="loadmore"]', '[class*="show-more"]',
                'button:contains("加载更多")', 'button:contains("查看更多")',
                'a:contains("加载更多")', 'a:contains("查看更多")',
                
                // 下拉菜单
                'select', '[role="combobox"]', '[class*="dropdown"]',
                '[class*="select"]', '.ant-select', '.el-select',
                '[aria-haspopup="listbox"]',
                
                // Tab 切换
                '[role="tab"]', '[class*="tab"]', '.ant-tabs-tab',
                '.el-tabs__item', '[data-tab]', '.nav-tabs a',
                
                // 筛选/搜索
                'form', '[type="search"]', '[class*="filter"]',
                '[class*="search"]', '.search-input', '.filter-option',
                'button[type="submit"]', '[role="searchbox"]',
                
                // 弹窗内按钮
                '.modal button', '.dialog button', '.popup button',
                '.ant-modal button', '.el-dialog button',
                '[role="dialog"] button', '[role="alertdialog"] button'
            ];
            
            // 使用文本内容匹配的元素
            const textPatterns = [
                { text: '加载更多', tags: ['button', 'a', 'div', 'span'] },
                { text: '查看更多', tags: ['button', 'a', 'div', 'span'] },
                { text: '展开', tags: ['button', 'a', 'div', 'span'] },
                { text: '收起', tags: ['button', 'a', 'div', 'span'] },
                { text: '下一页', tags: ['button', 'a'] },
                { text: '上一页', tags: ['button', 'a'] }
            ];
            
            const nodes = new Set();
            
            // 添加通过选择器找到的元素
            selectors.forEach(selector => {
                try {
                    document.querySelectorAll(selector).forEach(node => nodes.add(node));
                } catch (e) {
                    // 忽略无效选择器
                }
            });
            
            // 添加通过文本内容找到的元素
            textPatterns.forEach(({ text, tags }) => {
                tags.forEach(tag => {
                    document.querySelectorAll(tag).forEach(node => {
                        const nodeText = (node.innerText || node.textContent || '').trim();
                        if (nodeText.includes(text)) {
                            nodes.add(node);
                        }
                    });
                });
            });
            
            return Array.from(nodes).map(node => ({
                tag: node.tagName,
                selector: node.outerHTML,
                visible: !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length),
                text: node.innerText || node.value || '',
                priority: this._calculateElementPriority(node)
            }));
        });
    }
    
    /**
     * 计算元素优先级（用于快速模式）
     * @param {HTMLElement} node - DOM元素
     * @returns {number} 优先级分数（越小越优先）
     */
    _calculateElementPriority(node) {
        let priority = 100;
        
        const text = (node.innerText || node.textContent || '').trim().toLowerCase();
        const className = (node.className || '').toString().toLowerCase();
        
        // list query 相关元素优先级最高
        if (className.includes('pag') || className.includes('page')) priority -= 50;
        if (className.includes('load-more') || text.includes('加载更多') || text.includes('查看更多')) priority -= 50;
        if (className.includes('filter') || className.includes('search')) priority -= 40;
        if (node.tagName === 'SELECT' || className.includes('dropdown')) priority -= 40;
        if (className.includes('tab')) priority -= 30;
        
        // 导航和菜单
        if (className.includes('menu') || className.includes('nav')) priority -= 20;
        
        // 基本按钮和链接
        if (node.tagName === 'BUTTON') priority -= 10;
        if (node.tagName === 'A') priority -= 5;
        
        return priority;
    }

    /**
     * 使用LLM检测页面可交互元素
     * @param {PageWrapper} pageWrapper
     * @param {string} htmlContent
     * @returns {Promise<Array>} 元素数组
     */
    async detectWithLLM(htmlContent) {
    const LLMElementHelper = require('../llm/llm-element-helper');
    const llmHelper = new LLMElementHelper();
    let selectors = [];
    let formTestData = {};
    try {
        selectors = await llmHelper.getClickableSelectors(htmlContent);
        // 新增：自动生成表单测试数据
        formTestData = await llmHelper.generateFormTestData(htmlContent);
        console.log(`[ElementDetector] LLM检测到 ${selectors.length} 个可点击元素`);
    } catch (e) {
        console.error('[ElementDetector] LLM元素检测失败:', e);
        selectors = []; // 确保在出错时也是空数组
    }
    
    // 转换为标准元素对象数组格式，与detectStaticElements保持一致
    const clickableElements = selectors.map(sel => ({
        tag: '',
        selector: sel,
        visible: true,
        text: ''
    }));
    
    // 存储表单测试数据，供后续使用
    this.formTestData = formTestData;
    
    // 过滤掉资源/元信息标签（如<link rel="stylesheet">、<script>等）
    const filtered = clickableElements.filter(el => !this._isResourceOrMeta(el));
    
    // 只返回可点击元素数组，与detectStaticElements保持一致的返回格式
    return filtered;
}

    /**
     * 过滤重复元素
     * @param {Array} elements
     * @returns {Array}
     */
    filterDuplicates(elements) {
        const seen = new Set();
        return elements.filter(el => {
            const key = el.selector || el.tag + JSON.stringify(el);
            if (seen.has(key)) return false;
            seen.add(key);
            return true;
        });
    }

    /**
     * 元素分类（按钮、链接、表单等）
     * @param {Array} elements
     * @returns {Object} 分类结果
     */
    categorizeElements(elements) {
        const categories = { button: [], link: [], form: [], other: [] };
        for (const el of elements) {
            // 跳过资源/元信息标签
            if (this._isResourceOrMeta(el)) {
                continue;
            }

            const elTagLower = (el.tag || '').toLowerCase();
            const elSelectorLower = (el.selector || '').toLowerCase();
            
            if (el.tag === 'BUTTON' || /button/i.test(el.selector) || el.tag === 'INPUT' && /button|submit/i.test(elSelectorLower)) {
                categories.button.push(el);
            // 收紧“链接”判定为语义链接：<a> 或 role="link"
            } else if (
                el.tag === 'A' ||
                /^\s*<\s*a[\s>]/i.test(el.selector || '') ||
                /\brole\s*=\s*["']link["']/i.test(el.selector || '')
            ) {
                categories.link.push(el);
            } else if (el.tag === 'FORM' || /form/i.test(el.selector)) {
                categories.form.push(el);
            } else if (/input|select|textarea/i.test(elTagLower)) {
                categories.form.push(el);
            } else {
                // 改进的其他可交互元素检测
                const isClickable = (
                    /tabindex/i.test(elSelectorLower) || 
                    /role="(menuitem|button|link|option|tab)"/i.test(elSelectorLower) ||
                    /onclick/i.test(elSelectorLower) ||
                    /ant-menu-item|dropdown-trigger|menu-item/i.test(elSelectorLower) ||
                    /li\s+class="[^"]*item/i.test(elSelectorLower)
                );
                
                if (isClickable) {
                    categories.other.push(el);
                }
            }
        }
        return categories;
    }

    /**
     * 元素优先级排序
     * @param {Array} elements
     * @returns {Array}
     */
    prioritizeElements(elements) {
        // 增强的优先级排序，list query 相关元素优先
        const getPriority = el => {
            if (!el) return 1000;
            
            // 如果元素已经有优先级分数，直接使用
            if (typeof el.priority === 'number') return el.priority;
            
            const selector = (el.selector || '').toLowerCase();
            const text = (el.text || '').toLowerCase();
            let priority = 100;
            
            // list query 相关元素优先级最高
            if (/pag|page|next|prev/i.test(selector)) priority -= 50;
            if (/load-more|loadmore|show-more/i.test(selector) || 
                text.includes('加载更多') || text.includes('查看更多')) priority -= 50;
            if (/filter|search/i.test(selector)) priority -= 40;
            if (el.tag === 'SELECT' || /dropdown|select|combobox/i.test(selector)) priority -= 40;
            if (/tab/i.test(selector)) priority -= 30;
            
            // 导航和菜单
            if (/menu|nav/i.test(selector)) priority -= 20;
            
            // 基本按钮和链接
            if (el.tag === 'BUTTON' || /button/i.test(selector)) priority -= 10;
            if (el.tag === 'A') priority -= 5;
            
            return priority;
        };
        
        return elements.slice().sort((a, b) => getPriority(a) - getPriority(b));
    }

    // 资源/元信息元素判定：<link>、<script>、<meta>、<base>、<style> 或 rel="stylesheet"
    _isResourceOrMeta(elOrSelector) {
        const tagUpper = (typeof elOrSelector === 'object' && elOrSelector)
            ? ((elOrSelector.tag || '').toUpperCase())
            : '';
        const selectorLower = (typeof elOrSelector === 'object' && elOrSelector)
            ? ((elOrSelector.selector || '').toLowerCase())
            : (typeof elOrSelector === 'string' ? elOrSelector.toLowerCase() : '');
        if (['LINK','SCRIPT','META','BASE','STYLE'].includes(tagUpper)) return true;
        if (/^<\s*(link|script|meta|base|style)\b/.test(selectorLower)) return true;
        if (/\brel\s*=\s*["']stylesheet["']/.test(selectorLower)) return true;
        return false;
    }
}

module.exports = ElementDetector;
