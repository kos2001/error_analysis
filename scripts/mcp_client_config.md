# MCP 클라이언트 연결 설정

두 방식 중 하나를 고른다. **배포에는 HTTP 방식**이 낫다 — 클라이언트에 이
저장소도 파이썬 환경도 필요 없다.

## 1. HTTP (배포용, 권장)

서버가 `/mcp/` 를 직접 제공한다. 필요한 것은 URL 과 토큰뿐이다(슬래시를 빠뜨린 `/mcp` 도 307 로 넘겨준다).

토큰 발급 — 웹에 로그인한 뒤:

```sh
curl -X POST https://<서버>/auth/token \
  -H 'Content-Type: application/json' -b <세션쿠키> \
  -d '{"days":30,"label":"내 노트북 Claude Code"}'
```

`~/.claude.json` (Claude Code) 또는 클라이언트의 MCP 설정:

```json
{
  "mcpServers": {
    "lsi-error-analysis": {
      "type": "http",
      "url": "https://<서버>/mcp/",
      "headers": { "Authorization": "Bearer <발급받은 토큰>" }
    }
  }
}
```

원격 배포라면 서버에 허용 Host 를 지정한다(DNS 리바인딩 보호):

```sh
LSI_MCP_ALLOWED_HOSTS=lsi.example.com,lsi.example.com:443
```

## 2. stdio (로컬 개발용)

클라이언트가 이 저장소의 프로세스를 직접 띄운다.

```json
{
  "mcpServers": {
    "lsi-error-analysis": {
      "command": "/절대경로/lsi_error_analyzer/.venv/bin/python",
      "args": ["/절대경로/lsi_error_analyzer/src/mcp_server.py"],
      "env": {
        "LSI_API": "http://127.0.0.1:8001",
        "LSI_MCP_TOKEN": "<발급받은 토큰>"
      }
    }
  }
}
```

인증이 비활성(인가 목록 없음)인 로컬 환경에서는 `LSI_MCP_TOKEN` 없이도 동작한다.

## 연결 확인

클라이언트에서 `whoami` 도구를 부르면 지금 어떤 신원·역할로 붙어 있는지 나온다.
`인증 실패(401)` 가 나오면 토큰이 없거나 만료·폐기된 것이다.

## 토큰 폐기

토큰은 서버에 저장하지 않으므로 개별 폐기는 없다. 두 가지 수단이 있다:

- **사용자 회수** (설정 → 사용자 관리 → 회수) — 그 사람의 모든 토큰이 즉시 무효.
  토큰은 신원만 담고 권한은 매 요청 인가 목록에서 다시 읽기 때문이다.
- **`RVP_SESSION_SECRET` 교체** — 전체 무효화(웹 세션도 함께 끊긴다).
