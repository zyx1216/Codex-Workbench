// 本地 CORS 代理：把浏览器请求转发到火山方舟，绕过浏览器跨域限制
// 运行：node proxy.js
// 然后在工作台 AI 设置里，API 地址填：
//   数据面：  http://localhost:8787/api/v3/chat/completions
//   Agent Plan(OpenAI兼容): http://localhost:8787/api/plan/v3/chat/completions
// Key 和模型照常填真实值即可。

const http = require('http');
const https = require('https');

const PORT = 8787;
const TARGET_HOST = 'ark.cn-beijing.volces.com';

const server = http.createServer((req, res) => {
  // 允许任意来源跨域
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET,POST,OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type,Authorization');
  res.setHeader('Access-Control-Max-Age', '86400');

  // 预检请求直接返回
  if (req.method === 'OPTIONS') {
    res.writeHead(204);
    res.end();
    return;
  }

  let body = '';
  req.on('data', chunk => { body += chunk; });
  req.on('end', () => {
    const opts = {
      hostname: TARGET_HOST,
      path: req.url,
      method: req.method,
      headers: {
        'Content-Type': 'application/json',
        'Authorization': req.headers['authorization'] || '',
        'Content-Length': Buffer.byteLength(body),
      },
    };

    const proxyReq = https.request(opts, proxyRes => {
      // 透传状态码和响应体，响应头也补上 CORS
      res.writeHead(proxyRes.statusCode || 502, {
        'Content-Type': 'application/json; charset=utf-8',
        'Access-Control-Allow-Origin': '*',
      });
      proxyRes.pipe(res);
    });

    proxyReq.on('error', err => {
      res.writeHead(502, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'proxy_error', message: err.message }));
    });

    if (body) proxyReq.write(body);
    proxyReq.end();
  });
});

server.listen(PORT, () => {
  console.log('========================================');
  console.log('  方舟 CORS 代理已启动');
  console.log('  本地地址: http://localhost:' + PORT);
  console.log('  转发目标: https://' + TARGET_HOST);
  console.log('========================================');
  console.log('在工作台 AI 设置里填写：');
  console.log('  数据面        -> http://localhost:' + PORT + '/api/v3/chat/completions');
  console.log('  Agent Plan    -> http://localhost:' + PORT + '/api/plan/v3/chat/completions');
  console.log('(保持本窗口运行，关闭即停止代理)');
});
