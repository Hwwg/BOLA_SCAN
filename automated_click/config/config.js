// 全局配置文件
module.exports = {
    // 扫描运行控制
    scanControl: {
        // autoclick 默认总执行时长：3 分钟
        defaultMaxDurationMs: 3 * 60 * 1000
    },

    // URL跳转白名单配置
    urlWhitelist: {
        // 是否启用白名单功能
        enabled: true,
        
        // 允许跳转的域名列表
        // 支持完整域名匹配和通配符匹配
        allowedDomains: [
            process.env.BOLASCAN_ALLOWED_DOMAIN || "example.test"
            // 示例：允许跳转到这些域名
            // 'login.example.com',
            // 'auth.example.com', 
            // 'sso.example.com',
            // '*.example.com'  // 通配符匹配
        ],
        
        // 允许跳转的URL路径模式
        allowedPaths: [
            // 示例：允许跳转到包含这些路径的URL
            // '/login',
            // '/auth',
            // '/sso',
            // '/oauth'
        ],
        
        // 是否允许同协议跳转（http->http, https->https）
        allowSameProtocol: true,
        
        // 是否允许HTTPS到HTTP的跳转（不推荐，安全风险）
        allowHttpsToHttp: false
    }
};
