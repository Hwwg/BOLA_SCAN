#!/usr/bin/env node

const path = require('path');
const MainModule = require('./modules/main-module');

function parseArguments() {
    const argv = process.argv.slice(2);
    const args = {
        url: '',
        depth: 2,
        output: './results',
        credentials: null,
        format: 'json',
        token: null,
        tokenHeader: 'Authorization',
        tokenPrefix: 'Bearer ',
        tokenStorageKey: null,
        tokenStorageTarget: 'local', // 'local' 或 'session'
        tokenCookieName: null,
        mode: 'desktop', // 新增：运行模式，desktop 或 mobile
        fastMode: false, // 快速并行扫描模式
        parallelPages: 3 // 并行扫描页面数量
    };
    for (let i = 0; i < argv.length; i++) {
        if (argv[i] === '--url' && argv[i + 1]) args.url = argv[++i];
        else if (argv[i] === '--depth' && argv[i + 1]) args.depth = parseInt(argv[++i], 10);
        else if (argv[i] === '--output' && argv[i + 1]) args.output = argv[++i];
        else if (argv[i] === '--username' && argv[i + 1]) {
            if (!args.credentials) args.credentials = {};
            args.credentials.username = argv[++i];
        }
        else if (argv[i] === '--password' && argv[i + 1]) {
            if (!args.credentials) args.credentials = {};
            args.credentials.password = argv[++i];
        }
        else if (argv[i] === '--format' && argv[i + 1]) {
            args.format = argv[++i];
        }
        else if (argv[i] === '--token' && argv[i + 1]) {
            args.token = argv[++i];
        }
        else if (argv[i] === '--token-header' && argv[i + 1]) {
            args.tokenHeader = argv[++i];
        }
        else if (argv[i] === '--token-prefix' && argv[i + 1]) {
            args.tokenPrefix = argv[++i];
        }
        else if (argv[i] === '--token-storage-key' && argv[i + 1]) {
            args.tokenStorageKey = argv[++i];
        }
        else if (argv[i] === '--token-storage-target' && argv[i + 1]) {
            const val = argv[++i].toLowerCase();
            args.tokenStorageTarget = (val === 'session') ? 'session' : 'local';
        }
        else if (argv[i] === '--token-cookie-name' && argv[i + 1]) {
            args.tokenCookieName = argv[++i];
        }
        else if (argv[i] === '--mode' && argv[i + 1]) {
            const val = argv[++i].toLowerCase();
            args.mode = (val === 'mobile') ? 'mobile' : 'desktop';
        }
        else if (argv[i] === '--fast-mode') {
            args.fastMode = true;
        }
        else if (argv[i] === '--parallel-pages' && argv[i + 1]) {
            args.parallelPages = parseInt(argv[++i], 10);
        }
    }
    if (!args.url) {
        console.error('Usage: node index.js --url <target_url> [--depth <max_depth>] [--output <output_path>] [--username <username>] [--password <password>] [--format <json|csv>] [--token <token>] [--token-header <headerName>] [--token-prefix <prefix>] [--token-storage-key <key>] [--token-storage-target <local|session>] [--token-cookie-name <name>] [--mode <desktop|mobile>] [--fast-mode] [--parallel-pages <number>]');
        process.exit(1);
    }
    return args;
}

function initialize(args) {
    // 可扩展：初始化日志、配置、依赖注入等
    return {
        mainModule: new MainModule({
            startUrl: args.url,
            maxDepth: args.depth,
            outputPath: path.resolve(args.output),
            credentials: args.credentials,
            token: args.token,
            tokenHeader: args.tokenHeader,
            tokenPrefix: args.tokenPrefix,
            tokenStorageKey: args.tokenStorageKey,
            tokenStorageTarget: args.tokenStorageTarget,
            tokenCookieName: args.tokenCookieName,
            mode: args.mode,
            fastMode: args.fastMode,
            parallelPages: args.parallelPages
        }),
        args
    };
}

async function startScan(mainModule, args) {
    try {
        // 注册进程结束前的保存操作
        process.on('SIGINT', async () => {
            console.log('\n[System] 检测到用户中断，正在保存捕获的请求数据...');
            if (mainModule.requestCapture) {
                await mainModule.requestCapture.saveResults(args.output);
                console.log('[System] 已保存请求数据到', args.output);
            }
            process.exit(0);
        });
        
        // 注册未处理的异常处理器
        process.on('uncaughtException', async (err) => {
            console.error('[System] 未捕获的异常:', err);
            console.log('[System] 正在尝试保存已捕获的请求数据...');
            if (mainModule.requestCapture) {
                await mainModule.requestCapture.saveResults(args.output);
                console.log('[System] 已保存请求数据到', args.output);
            }
            process.exit(1);
        });
        
        // 根据模式选择扫描方法
        if (args.fastMode) {
            console.log(`[System] 启动快速并行扫描模式 (并行页面数: ${args.parallelPages})`);
            await mainModule.scanFast(mainModule.options.startUrl, mainModule.options.maxDepth);
        } else {
            await mainModule.scan(mainModule.options.startUrl, mainModule.options.maxDepth);
        }
        mainModule.resultManager.exportResults && mainModule.resultManager.exportResults(args.format);
        console.log('扫描完成！');
    } catch (err) {
        console.error('扫描过程中发生错误:', err);
        // 发生错误时也尝试保存已捕获的请求
        if (mainModule.requestCapture) {
            console.log('[System] 正在保存捕获的请求数据...');
            await mainModule.requestCapture.saveResults(args.output);
            console.log('[System] 已保存请求数据到', args.output);
        }
    }
}

(async () => {
    const { mainModule, args } = initialize(parseArguments());
    await startScan(mainModule, args);
})();