Run the AEO health check. Check all services and report a summary table:
1. Runner API (curl http://localhost:5001/health)
2. Device Manager (curl http://localhost:8080/device/list)
3. Connected devices (adb devices)
4. Socksdroid installed per device
5. Proxy credentials (grep PROXY_PASSWORD ~/projects/aeo-appium/.env)
6. DeepSeek key (echo $DEEPSEEK_API_KEY, if empty try source ~/.zshrc first)