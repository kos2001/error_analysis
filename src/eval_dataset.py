"""Synthetic validation dataset for RVP support agent.

Each case has:
    - input: customer message
    - expected_facts: ground-truth facts the answer should contain
    - category: knowledge area being tested
"""
from __future__ import annotations

DATASET = [
    {
        "id": "install-led-amber",
        "category": "installation",
        "input": "RVP-Cam-200을 켰는데 LED가 60초 넘게 주황색에 머물러 있어요. 어떻게 해야 하나요?",
        "expected_facts": [
            "device cannot reach the cloud",
            "outbound port 443",
            "outbound port 8883",
        ],
    },
    {
        "id": "error-e022",
        "category": "error-code",
        "input": "What does error E022 mean and how do I fix it?",
        "expected_facts": [
            "Edge model corrupted",
            "Re-download model",
            "Settings > Device > Maintenance",
        ],
    },
    {
        "id": "error-e033",
        "category": "error-code",
        "input": "디바이스에서 E033 에러가 떴습니다.",
        "expected_facts": [
            "thermal throttling",
            "85",
            "75",
            "ventilation",
        ],
    },
    {
        "id": "refund-policy",
        "category": "billing",
        "input": "I bought Pro 5 days ago and used about 200 inferences. Can I get a refund?",
        "expected_facts": [
            "14 days",
            "below 1000",
            "refundable",
        ],
    },
    {
        "id": "refund-escalate",
        "category": "escalation",
        "input": "I want a $350 refund for my Enterprise add-on, the model accuracy was terrible.",
        "expected_facts": [
            "L2",
            "specialist",
            "escalate",
        ],
    },
    {
        "id": "rate-limit",
        "category": "api",
        "input": "What's the Pro plan API rate limit and what happens if I exceed it?",
        "expected_facts": [
            "100 req/s",
            "429",
        ],
    },
    {
        "id": "data-deletion",
        "category": "privacy",
        "input": "고객 데이터 전체 삭제는 어떻게 요청하나요? 처리 기간도 알려주세요.",
        "expected_facts": [
            "Dashboard",
            "Privacy",
            "30",
        ],
    },
    {
        "id": "low-light",
        "category": "error-code",
        "input": "Getting E101 in our warehouse aisle - it's pretty dim there.",
        "expected_facts": [
            "50",
            "lux",
            "IR mode",
        ],
    },
    {
        "id": "webhook-verify",
        "category": "api",
        "input": "How do I verify webhook signatures?",
        "expected_facts": [
            "HMAC-SHA256",
            "X-RVP-Signature",
        ],
    },
    {
        "id": "proactive-latency",
        "category": "proactive",
        "input": "고객이 연락하기 전에 지연 시간 문제가 자동으로 감지되나요?",
        "expected_facts": [
            "500",
            "3",
            "ticket",
        ],
    },
]
