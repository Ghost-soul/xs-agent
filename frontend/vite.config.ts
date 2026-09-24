import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const localToken = env.NOVEL_WRITER_LOCAL_TOKEN;

  return {
    plugins: [react()],
    server: {
      host: "127.0.0.1",
      port: 5173,
      strictPort: true,
      proxy: {
        "/backend": {
          target: "http://127.0.0.1:8000",
          rewrite: (path: string) => path.replace(/^\/backend/, ""),
          configure: (proxy) => {
            proxy.on("proxyReq", (proxyRequest, request) => {
              if (!localToken) return;
              proxyRequest.setHeader("Authorization", "Bearer " + localToken);
              if (["GET", "HEAD", "OPTIONS"].indexOf(request.method ?? "GET") === -1) {
                proxyRequest.setHeader("X-CSRF-Token", localToken);
              }
            });
          },
        },
      },
    },
  };
});
