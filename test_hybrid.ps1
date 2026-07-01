$body = @{
  model = "m365-copilot"
  messages = @(@{ role = "user"; content = "Say hello in one short sentence." })
} | ConvertTo-Json -Depth 10
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/v1/chat/completions" -ContentType "application/json" -Body $body
