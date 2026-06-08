"""Expanded validation dataset (20 cases) covering more categories and edge cases."""
from __future__ import annotations

DATASET_LARGE = [
    # --- installation / errors (8) ---
    {
        "id": "install-led-amber",
        "category": "installation",
        "input": "RVP-Cam-200을 켰는데 LED가 60초 넘게 주황색에 머물러 있어요.",
        "expected_facts": ["cloud", "443", "8883"],
    },
    {
        "id": "install-qr-fail",
        "category": "installation",
        "input": "How do I pair the device if the QR code scan fails?",
        "expected_facts": ["12-digit serial", "manually"],
    },
    {
        "id": "install-calibration",
        "category": "installation",
        "input": "초기 캘리브레이션은 얼마나 걸리고, 도중에 전원을 끄면 어떻게 되나요?",
        "expected_facts": ["90 seconds", "Do not unplug"],
    },
    {
        "id": "error-e001",
        "category": "error-code",
        "input": "PoE 카메라에서 E001이 떴어요.",
        "expected_facts": ["802.3af", "15.4W"],
    },
    {
        "id": "error-e014",
        "category": "error-code",
        "input": "E014 means what? Network is fine.",
        "expected_facts": ["auth", "re-pair", "API key"],
    },
    {
        "id": "error-e022",
        "category": "error-code",
        "input": "What does E022 mean and how do I fix it?",
        "expected_facts": ["Edge model", "Re-download", "Maintenance"],
    },
    {
        "id": "error-e033",
        "category": "error-code",
        "input": "E033 떴습니다.",
        "expected_facts": ["85", "75", "ventilation"],
    },
    {
        "id": "error-e101",
        "category": "error-code",
        "input": "Warehouse에서 E101 자꾸 떠요. 어둡긴 합니다.",
        "expected_facts": ["50", "lux", "IR mode"],
    },
    # --- billing / refund / plans (4) ---
    {
        "id": "refund-policy",
        "category": "billing",
        "input": "I bought Pro 5 days ago and used 200 inferences. Can I get a refund?",
        "expected_facts": ["14 days", "1000", "refundable"],
    },
    {
        "id": "refund-escalate",
        "category": "escalation",
        "input": "$350 refund 환불 요청합니다. Enterprise 부가서비스 정확도 문제로요.",
        "expected_facts": ["L2", "escalate"],
    },
    {
        "id": "plans-compare",
        "category": "billing",
        "input": "Free vs Pro 가격이랑 inference 한도 차이 알려주세요.",
        "expected_facts": ["29", "100", "10"],
    },
    {
        "id": "failed-payment",
        "category": "billing",
        "input": "결제 실패하면 서비스가 어떻게 되나요?",
        "expected_facts": ["Free", "7 days"],
    },
    # --- API / integration (3) ---
    {
        "id": "rate-limit",
        "category": "api",
        "input": "What's the Pro plan API rate limit and what happens if I exceed it?",
        "expected_facts": ["100", "429"],
    },
    {
        "id": "webhook-verify",
        "category": "api",
        "input": "How do I verify webhook signatures?",
        "expected_facts": ["HMAC-SHA256", "X-RVP-Signature"],
    },
    {
        "id": "websocket",
        "category": "api",
        "input": "실시간 스트리밍 endpoint URL 알려주세요.",
        "expected_facts": ["wss://", "device_id"],
    },
    # --- privacy / data (2) ---
    {
        "id": "data-deletion",
        "category": "privacy",
        "input": "고객 데이터 전체 삭제 요청은 어떻게 하고 처리 기간은 얼마인가요?",
        "expected_facts": ["Dashboard", "Privacy", "30"],
    },
    {
        "id": "data-region",
        "category": "privacy",
        "input": "Where is my video data processed?",
        "expected_facts": ["US", "EU", "KR"],
    },
    # --- multimodal / proactive (3) ---
    {
        "id": "mounting-angle",
        "category": "multimodal",
        "input": "카메라가 기울어져 있으면 어느 정도부터 문제가 되나요?",
        "expected_facts": ["15", "horizontal", "false negative"],
    },
    {
        "id": "image-format",
        "category": "multimodal",
        "input": "설치 사진 업로드할 때 허용되는 포맷이랑 크기 제한은요?",
        "expected_facts": ["JPEG", "PNG", "8"],
    },
    {
        "id": "proactive-latency",
        "category": "proactive",
        "input": "고객이 연락하기 전에 지연시간 문제가 자동 감지되나요?",
        "expected_facts": ["500", "3", "ticket"],
    },
]
