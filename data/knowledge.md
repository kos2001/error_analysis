# Robot Vision Platform — Customer Knowledge Base

## 1. Product Overview
Robot Vision Platform (RVP) is a B2C/B2B vision system that connects industrial and consumer cameras to a cloud inference service for object detection, defect inspection, and pose estimation. Supported devices: RVP-Cam-100 (USB), RVP-Cam-200 (PoE), RVP-Edge-Box (Jetson Orin Nano).

## 2. Installation Guide
- Power on the device. The status LED transitions: red (boot) -> amber (network) -> green (ready).
- If the LED stays amber for over 60 seconds, the device cannot reach the cloud. Check that ports 443 and 8883 (MQTT/TLS) are open outbound.
- Pair the device using the RVP mobile app. Use the QR code on the device back panel. If the QR scan fails, enter the 12-digit serial manually.
- First-time calibration runs automatically and takes 90 seconds. Do not unplug the device during calibration.

## 3. Common Error Codes
- E001 (Camera not detected): Reconnect USB cable; on PoE models, verify 802.3af switch supports 15.4W minimum.
- E014 (Cloud sync failure): Network reachable but auth failed. Re-pair the device or rotate the API key in the dashboard.
- E022 (Model load failure): Edge model corrupted. Trigger "Re-download model" from Settings > Device > Maintenance.
- E033 (Thermal throttling): Device exceeded 85°C. Improve ventilation; firmware will auto-resume when below 75°C.
- E101 (Insufficient illumination): Scene lux < 50. Add lighting or enable IR mode on RVP-Cam-200.

## 4. Subscription and Billing
- Plans: Free (100 inferences/day), Pro ($29/mo, 10k inferences/day), Enterprise (custom).
- Refunds: Pro plan is refundable within 14 days of purchase if total inference usage is below 1000.
- Failed payment: Service degrades to Free tier after 7 days of failed billing. Re-activate by updating the card on file.
- Cancellation: Self-service in Dashboard > Billing. Cancellation takes effect at end of current billing cycle.

## 5. API and Integration
- REST endpoint: https://api.rvp.example.com/v1/inference
- WebSocket stream: wss://stream.rvp.example.com/v1/{device_id}
- Rate limit: Pro = 100 req/s, Enterprise = 1000 req/s. Bursts >2x limit return HTTP 429.
- Webhook delivery uses HMAC-SHA256; verify the X-RVP-Signature header.

## 6. Privacy and Data
- Video frames are processed in-region (US, EU, KR). Default region inferred from device IP at pairing time.
- Customers can request full data deletion via Dashboard > Privacy > Delete All Data. SLA: 30 days.
- On-device inference (RVP-Edge-Box) keeps frames local; only metadata is sent to cloud.

## 7. Escalation Policy
- L1 (AI agent): All routine queries — installation, error codes, billing FAQ, account settings.
- L2 (Human specialist): Refund disputes >$200, hardware RMA, custom-model training, security incidents.
- L3 (Engineering): Reported model accuracy regressions, suspected platform outages.

## 8. Multimodal Diagnostics
- Customers may upload installation photos or screenshots of error dialogs. The agent uses vision to identify:
  - Mounting angle issues (camera tilted >15 degrees off horizontal can cause false negatives).
  - LED state mismatches (e.g., user says "green" but photo shows amber).
  - Cable seating problems on PoE/USB ports.
- Recommended image format: JPEG/PNG, max 8MB.

## 9. Proactive Monitoring
- Device telemetry includes CPU temp, frame rate, model latency, and packet loss.
- If model latency exceeds 500ms for 3 consecutive minutes, the platform auto-opens a support ticket and notifies the customer with remediation steps.
- Customers can opt out of proactive notifications in Dashboard > Notifications.
