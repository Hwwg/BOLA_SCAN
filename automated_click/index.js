#!/usr/bin/env node

const path = require('path');
const MainModule = require('./modules/main-module');

const DEFAULT_MAX_DURATION_MS = 3 * 60 * 1000;

function loadRuntimeConfig() {
    try {
        return require('./config/config');
    } catch (error) {
        console.warn(`[System] 加载配置文件失败，使用默认扫描时长: ${error.message}`);
        return {};
    }
}

function toPositiveInt(value, fallback) {
    const parsed = Number.parseInt(value, 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function parseArguments() {
    const argv = process.argv.slice(2);
    const runtimeConfig = loadRuntimeConfig();
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
        parallelPages: 3, // 并行扫描页面数量
        maxDurationMs: toPositiveInt(
            runtimeConfig?.scanControl?.defaultMaxDurationMs,
            DEFAULT_MAX_DURATION_MS
        )
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
        else if (argv[i] === '--max-duration-ms' && argv[i + 1]) {
            args.maxDurationMs = toPositiveInt(argv[++i], args.maxDurationMs);
        }
        else if (argv[i] === '--max-duration-seconds' && argv[i + 1]) {
            args.maxDurationMs = toPositiveInt(argv[++i], Math.floor(args.maxDurationMs / 1000)) * 1000;
        }
    }
    if (!args.url) {
        console.error('Usage: node index.js --url <target_url> [--depth <max_depth>] [--output <output_path>] [--username <username>] [--password <password>] [--format <json|csv>] [--token <token>] [--token-header <headerName>] [--token-prefix <prefix>] [--token-storage-key <key>] [--token-storage-target <local|session>] [--token-cookie-name <name>] [--mode <desktop|mobile>] [--fast-mode] [--parallel-pages <number>] [--max-duration-ms <number>] [--max-duration-seconds <number>]');
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

async function saveCapturedRequests(mainModule, args, reason) {
    if (!mainModule.requestCapture) {
        return;
    }

    try {
        console.log(`[System] ${reason}，正在保存捕获的请求数据...`);
        await mainModule.requestCapture.saveResults(args.output);
        mainModule.requestCapture.cleanup && mainModule.requestCapture.cleanup();
        console.log('[System] 已保存请求数据到', args.output);
    } catch (error) {
        console.error('[System] 保存请求数据失败:', error);
    }
}

async function closeBrowser(mainModule) {
    try {
        if (mainModule._userActivityTimer) {
            clearInterval(mainModule._userActivityTimer);
            mainModule._userActivityTimer = null;
        }
        if (mainModule.pageWrapper && typeof mainModule.pageWrapper.close === 'function') {
            await mainModule.pageWrapper.close();
        }
    } catch (error) {
        console.error('[System] 关闭浏览器失败:', error);
    }
}

async function generateReport(mainModule) {
    try {
        if (mainModule.resultManager && typeof mainModule.resultManager.generateScanReport === 'function') {
            await mainModule.resultManager.generateScanReport();
        }
    } catch (error) {
        console.error('[System] 生成扫描报告失败:', error);
    }
}

async function startScan(mainModule, args) {
    let shutdownStarted = false;
    let scanTimer = null;

    const shutdown = async (exitCode, reason) => {
        if (shutdownStarted) {
            return;
        }
        shutdownStarted = true;

        if (scanTimer) {
            clearTimeout(scanTimer);
            scanTimer = null;
        }

        await saveCapturedRequests(mainModule, args, reason);
        await generateReport(mainModule);
        await closeBrowser(mainModule);
        process.exit(exitCode);
    };

    try {
        // 注册进程结束前的保存操作
        process.on('SIGINT', async () => {
            console.log('\n[System] 检测到用户中断');
            await shutdown(0, '检测到用户中断');
        });
        
        // 注册未处理的异常处理器
        process.on('uncaughtException', async (err) => {
            console.error('[System] 未捕获的异常:', err);
            await shutdown(1, '发生未捕获异常');
        });

        scanTimer = setTimeout(async () => {
            console.log(`\n[System] 已达到最大扫描时长 ${Math.floor(args.maxDurationMs / 1000)} 秒，自动停止 autoclick`);
            await shutdown(0, '达到最大扫描时长');
        }, args.maxDurationMs);

        if (typeof scanTimer.unref === 'function') {
            scanTimer.unref();
        }

        console.log(`[System] autoclick 最大运行时长: ${Math.floor(args.maxDurationMs / 1000)} 秒`);
        
        // 根据模式选择扫描方法
        if (args.fastMode) {
            console.log(`[System] 启动快速并行扫描模式 (并行页面数: ${args.parallelPages})`);
            await mainModule.scanFast(mainModule.options.startUrl, mainModule.options.maxDepth);
        } else {
            await mainModule.scan(mainModule.options.startUrl, mainModule.options.maxDepth);
        }

        if (scanTimer) {
            clearTimeout(scanTimer);
            scanTimer = null;
        }

        mainModule.resultManager.exportResults && mainModule.resultManager.exportResults(args.format);
        console.log('扫描完成！');
    } catch (err) {
        if (scanTimer) {
            clearTimeout(scanTimer);
            scanTimer = null;
        }
        console.error('扫描过程中发生错误:', err);
        await saveCapturedRequests(mainModule, args, '扫描过程中发生错误');
    }
}

(async () => {
    const { mainModule, args } = initialize(parseArguments());
    await startScan(mainModule, args);
})();
