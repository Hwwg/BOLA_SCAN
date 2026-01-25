// URL管理与队列
class UrlManager {
    constructor(options = {}) {
        this.maxDepth = options.maxDepth || 2;
        this.baseUrl = options.baseUrl || '';
        this.queue = [];
        this.processed = new Set();
        this.baseDomain = this._extractDomain(this.baseUrl);
        
        // 加载白名单配置
        this.config = options.config || this._loadConfig();
        console.log(`[UrlManager] 初始化，基础域名: ${this.baseDomain}`);
        console.log(`[UrlManager] 白名单功能: ${this.config.urlWhitelist.enabled ? '已启用' : '已禁用'}`);
    }
    
    _loadConfig() {
        try {
            return require('../config/config');
        } catch (error) {
            console.warn(`[UrlManager] 加载配置文件失败，使用默认配置: ${error.message}`);
            return {
                urlWhitelist: {
                    enabled: false,
                    allowedDomains: [],
                    allowedPaths: [],
                    allowSameProtocol: true,
                    allowHttpsToHttp: false
                }
            };
        }
    }

    _extractDomain(url) {
        try {
            const parsed = new URL(url);
            return parsed.hostname;
        } catch (e) {
            return '';
        }
    }
    
    /**
     * 检查URL是否在白名单中
     * @param {string} url - 要检查的URL
     * @returns {boolean} - 是否允许访问
     */
    _isUrlAllowed(url) {
        if (!this.config.urlWhitelist.enabled) {
            return false;
        }
        
        try {
            const parsedUrl = new URL(url);
            const baseUrl = new URL(this.baseUrl);
            
            // 检查协议限制
            if (!this._isProtocolAllowed(parsedUrl, baseUrl)) {
                return false;
            }
            
            // 检查域名白名单
            if (this._isDomainAllowed(parsedUrl.hostname)) {
                return true;
            }
            
            // 检查路径白名单
            if (this._isPathAllowed(parsedUrl.pathname)) {
                return true;
            }
            
            return false;
        } catch (error) {
            console.warn(`[UrlManager] URL解析失败: ${url}, ${error.message}`);
            return false;
        }
    }
    
    /**
     * 检查协议是否允许
     */
    _isProtocolAllowed(targetUrl, baseUrl) {
        const config = this.config.urlWhitelist;
        
        // 同协议跳转
        if (config.allowSameProtocol && targetUrl.protocol === baseUrl.protocol) {
            return true;
        }
        
        // HTTPS到HTTP的跳转
        if (baseUrl.protocol === 'https:' && targetUrl.protocol === 'http:') {
            return config.allowHttpsToHttp;
        }
        
        // HTTP到HTTPS的跳转（通常是安全的）
        if (baseUrl.protocol === 'http:' && targetUrl.protocol === 'https:') {
            return true;
        }
        
        return false;
    }
    
    /**
     * 检查域名是否在白名单中
     */
    _isDomainAllowed(hostname) {
        const allowedDomains = this.config.urlWhitelist.allowedDomains || [];
        
        for (const domain of allowedDomains) {
            // 完全匹配
            if (domain === hostname) {
                return true;
            }
            
            // 通配符匹配
            if (domain.startsWith('*.')) {
                const baseDomain = domain.substring(2);
                if (hostname.endsWith('.' + baseDomain) || hostname === baseDomain) {
                    return true;
                }
            }
        }
        
        return false;
    }
    
    /**
     * 检查路径是否在白名单中
     */
    _isPathAllowed(pathname) {
        const allowedPaths = this.config.urlWhitelist.allowedPaths || [];
        
        for (const path of allowedPaths) {
            if (pathname.startsWith(path)) {
                return true;
            }
        }
        
        return false;
    }

    processUrlBeforeAdd(url) {
        // 如果URL是相对路径，转为绝对URL
        try {
            return new URL(url, this.baseUrl).href;
        } catch (e) {
            return null;
        }
    }

    addUrl(url, depth) {
        if (!url) return;
        
        // 预处理URL
        const processedUrl = this.processUrlBeforeAdd(url);
        if (!processedUrl) return;
        
        // 检查是否已处理过
        if (this.hasUrlBeenProcessed(processedUrl)) return;
        
        // 检查是否同域名或在白名单中
        const urlDomain = this._extractDomain(processedUrl);
        if (this.baseDomain && urlDomain !== this.baseDomain) {
            // 检查是否在白名单中
            if (!this._isUrlAllowed(processedUrl)) {
                console.log(`[UrlManager] 跳过不同域URL (不在白名单中): ${processedUrl}`);
                return;
            } else {
                console.log(`[UrlManager] 允许跨域URL (在白名单中): ${processedUrl}`);
            }
        }
        
        console.log(`[UrlManager] 添加URL到队列: ${processedUrl} (深度: ${depth})`);
        this.queue.push({ url: processedUrl, depth });
    }

    getNextUrl() {
        return this.queue.shift();
    }

    markUrlProcessed(url) {
        this.processed.add(url);
    }

    hasUrlBeenProcessed(url) {
        return this.processed.has(url);
    }

    hasMoreUrls() {
        return this.queue.length > 0;
    }

    checkSameDomain(url, baseUrl = this.baseUrl) {
        try {
            const u = new URL(url, baseUrl);
            const b = new URL(baseUrl);
            return u.hostname === b.hostname;
        } catch {
            return false;
        }
    }
    
    /**
     * 获取已处理的URL数量
     * @returns {number} 已处理的URL数量
     */
    getProcessedCount() {
        return this.processed.size;
    }
}

module.exports = UrlManager;
