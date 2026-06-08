"""LSI 사업팀 칩/펌웨어 고객 고장 보고 — 현실적인 가짜 데이터셋 생성기.

시니어 엔지니어의 근본원인 분석/해결책을 코멘트로 포함하여, 주니어 엔지니어가
유사 고장 패턴을 학습할 수 있는 Jira 시드 데이터를 구성한다.

순수 Python(표준 라이브러리)만 사용. 결정론적 시드로 재현 가능.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

# ---------------------------------------------------------------------------
# 칩 제품 라인 정의 (LSI 사업부 가상 포트폴리오)
# ---------------------------------------------------------------------------


@dataclass
class ChipLine:
    code: str            # 내부 제품 코드
    family: str          # 제품군 (Jira component)
    desc_ko: str         # 한국어 설명
    fw_prefix: str       # 펌웨어 버전 prefix
    host_platforms: list[str]


CHIP_LINES: list[ChipLine] = [
    ChipLine(
        code="PM9C3-NVMe",
        family="SSD Controller",
        desc_ko="PCIe Gen5 NVMe SSD 컨트롤러",
        fw_prefix="GDC7",
        host_platforms=["Intel Raptor Lake", "AMD Genoa", "Ampere Altra", "Apple M-series NVMe host"],
    ),
    ChipLine(
        code="UFS4-Controller",
        family="UFS Controller",
        desc_ko="UFS 4.0 모바일 스토리지 컨트롤러",
        fw_prefix="UF40",
        host_platforms=["Snapdragon 8 Gen3", "Exynos 2400", "MediaTek Dimensity 9300"],
    ),
    ChipLine(
        code="ISOCELL-HP9",
        family="Image Sensor",
        desc_ko="200MP 모바일 CMOS 이미지센서",
        fw_prefix="ISP3",
        host_platforms=["Snapdragon 8 Gen3 ISP", "Exynos 2400 ISP", "Custom DSC bridge"],
    ),
    ChipLine(
        code="MDM-5400",
        family="5G Modem",
        desc_ko="5G NR Sub-6/mmWave 모뎀 IP",
        fw_prefix="MDM5",
        host_platforms=["Reference RFFE", "Skyworks frontend", "Qorvo frontend"],
    ),
    ChipLine(
        code="DDI-OLED-T7",
        family="Display Driver IC",
        desc_ko="OLED 패널 디스플레이 구동 IC (DDI)",
        fw_prefix="DDIT",
        host_platforms=["MIPI DSI v1.2 host", "eDP bridge", "Foldable panel module"],
    ),
    ChipLine(
        code="PMIC-S2MPS27",
        family="PMIC",
        desc_ko="멀티채널 전력관리 IC",
        fw_prefix="PMC2",
        host_platforms=["AP main rail", "Camera sub-PMIC", "Display sub-PMIC"],
    ),
    ChipLine(
        code="NPU-EdgeX2",
        family="NPU",
        desc_ko="엣지 추론용 신경망 처리장치",
        fw_prefix="NPX2",
        host_platforms=["Linux 6.6 + vendor driver", "Android NNAPI", "RTOS bare-metal"],
    ),
    ChipLine(
        code="LPDDR5X-PHY",
        family="Memory PHY",
        desc_ko="LPDDR5X 메모리 인터페이스 PHY",
        fw_prefix="DPHY",
        host_platforms=["8533Mbps host", "7500Mbps host", "Automotive grade host"],
    ),
    ChipLine(
        code="AUTO-MCU-V9",
        family="Automotive MCU",
        desc_ko="차량용 ASIL-D MCU (ADAS 도메인)",
        fw_prefix="AMV9",
        host_platforms=["AUTOSAR CP", "Zonal gateway", "ADAS sensor fusion ECU"],
    ),
    ChipLine(
        code="SE-Secure7",
        family="Secure Element",
        desc_ko="eSE/eSIM 보안 요소 칩",
        fw_prefix="SEC7",
        host_platforms=["NFC controller", "AP SPI host", "GlobalPlatform stack"],
    ),
]

# ---------------------------------------------------------------------------
# 고장 시나리오 템플릿 (제품군별 현실적 증상 + 근본원인 + 해결책)
# ---------------------------------------------------------------------------


@dataclass
class FailureTemplate:
    family: str
    summary: str           # {chip} 치환자 포함
    symptom: str
    repro: list[str]
    severity: str          # Blocker/Critical/Major/Minor
    category: str          # Firmware / Hardware / Thermal / Signal Integrity / Power / Timing / Security
    root_cause: str        # 시니어 근본원인 분석
    resolution: str        # 적용된 수정/해결책
    workaround: str        # 임시 우회책
    log_excerpt: str       # 첨부 로그 발췌


FAILURE_TEMPLATES: list[FailureTemplate] = [
    # ---- SSD Controller ----
    FailureTemplate(
        family="SSD Controller",
        summary="[{chip}] 고온 지속 쓰기 중 NVMe 컨트롤러 timeout 후 link down",
        symptom="장시간(>30분) 순차 쓰기 부하 시 드라이브가 사라지고 host에서 AER(Advanced Error Reporting) correctable error 폭주 후 link이 Gen1으로 강등됩니다.",
        repro=["fio 순차 쓰기 QD32 128K 30분 지속", "주변온도 45°C 챔버", "방열판 미장착 레퍼런스 보드"],
        severity="Critical",
        category="Thermal",
        root_cause="thermal throttle 진입 시 FW의 PCIe PHY recalibration 루틴이 host의 LTSSM 상태와 race를 일으켜 recovery 진입 실패. throttle threshold(85°C) 도달 시점에 background GC가 동시 동작하며 die 전류가 spike하여 PHY 전압이 droop된 것이 1차 trigger.",
        resolution="FW에서 thermal throttle 진입 전 GC를 선제적으로 suspend하고, PHY recalibration을 host의 L0 상태에서만 수행하도록 시퀀스 변경. throttle hysteresis를 5°C 추가.",
        workaround="방열판 장착 또는 호스트에서 ASPM L1 비활성화 시 재현되지 않음.",
        log_excerpt="nvme0: I/O timeout QID 3\npcieport: AER: Corrected error received\nnvme0: Link downgraded to 2.5GT/s x4",
    ),
    FailureTemplate(
        family="SSD Controller",
        summary="[{chip}] 비정상 전원차단 반복 후 일부 LBA에서 UNC(uncorrectable) 에러",
        symptom="갑작스런 전원차단(PLP 테스트)을 1000회 반복하면 특정 LBA 범위 읽기 시 uncorrectable error가 발생하고 SMART의 media error 카운터가 증가합니다.",
        repro=["쓰기 도중 무작위 hard power cut 1000 cycle", "PLP 커패시터 정상 충전 확인됨", "최신 FW에서도 재현"],
        severity="Blocker",
        category="Firmware",
        root_cause="power loss 직전 진행 중이던 mapping table(L2P) flush가 partial로 기록되고, 복구 시 journal replay 순서가 잘못되어 일부 mapping entry가 stale page를 가리킴. 원인은 journal commit과 user data program 간 ordering barrier 누락.",
        resolution="L2P journal에 단조증가 sequence number와 CRC를 추가하고, replay 시 sequence 역전 감지하면 해당 슈퍼블록을 재구성. program-before-journal ordering을 FTL에 강제.",
        workaround="없음. 데이터 무결성 이슈로 FW 업데이트 필수.",
        log_excerpt="FTL: journal replay seq mismatch (got 0x4A1, expected 0x4A2)\nnvme0: Read(0x02) UNC LBA 0x1F3A200",
    ),
    FailureTemplate(
        family="SSD Controller",
        summary="[{chip}] Deterministic TRIM 이후 읽기 데이터가 0이 아닌 garbage 반환",
        symptom="RZAT(Read Zero After Trim)을 보장한다고 명시했으나, deallocate 후 동일 LBA 읽기 시 이전 데이터가 반환되는 경우가 약 0.01% 확률로 발생합니다.",
        repro=["대용량 dataset deallocate", "즉시 동일 영역 read", "멀티스레드 32 thread 동시"],
        severity="Major",
        category="Firmware",
        root_cause="deallocate 명령이 L2P에서 unmap되기 전에 동일 LBA로 들어온 read가 race로 old mapping을 조회. unmap과 read 경로가 서로 다른 lock domain을 사용.",
        resolution="LBA 단위 read-after-unmap ordering을 위해 unmap 완료를 read 경로에서 확인하는 generation counter 도입.",
        workaround="TRIM 후 명시적 flush + 짧은 지연 삽입 시 미발생.",
        log_excerpt="trace: unmap(LBA=0x80000 len=0x1000) issued\ntrace: read(LBA=0x80000) served from stale PPN 0x2233",
    ),
    # ---- UFS Controller ----
    FailureTemplate(
        family="UFS Controller",
        summary="[{chip}] 저온 부팅 시 UFS link startup 실패로 부팅 행(hang)",
        symptom="-10°C 이하 cold boot에서 약 3% 빈도로 UFS link startup(DME) 실패가 발생해 디바이스가 부팅되지 않습니다.",
        repro=["-20°C 냉동 후 cold boot 100회", "host UniPro v1.8", "HS-G4 모드"],
        severity="Critical",
        category="Signal Integrity",
        root_cause="저온에서 RX termination 임피던스가 사양 상한으로 이동, HS-G4 startup 시 host의 default ADAPT 길이가 부족하여 CDR lock 실패. FW의 link startup retry가 동일 PWM 파라미터로만 재시도.",
        resolution="link startup 실패 시 HS-G3로 fallback 후 재협상하는 retry ladder를 FW에 추가하고, 저온에서 ADAPT preamble 길이를 동적으로 연장.",
        workaround="부팅 전 사전 가열 또는 HS-G3 강제 시 회피 가능.",
        log_excerpt="ufshcd: Link startup failed (err = -110)\nUniPro: DME_LINKSTARTUP timeout, retry 3/3",
    ),
    FailureTemplate(
        family="UFS Controller",
        summary="[{chip}] 고부하 카메라 버스트 촬영 중 UFS write latency spike(>500ms)",
        symptom="연사 촬영처럼 짧은 시간 대량 쓰기 시 간헐적으로 write latency가 500ms를 초과하여 프레임 드롭이 발생합니다.",
        repro=["4K 버스트 30fps 동시 저장", "device 80% 이상 사용", "백그라운드 앱 다수"],
        severity="Major",
        category="Firmware",
        root_cause="device가 거의 가득 찬 상태에서 urgent GC가 host write와 같은 우선순위로 스케줄되어 host 명령이 GC 뒤에 큐잉됨. HPB(Host Performance Booster) region eviction도 동시 발생.",
        resolution="urgent GC를 host idle window로 분산하고, write boost용 SLC cache 크기를 동적 조정. host에 bkops level hint를 정확히 보고하도록 수정.",
        workaround="저장 공간 20% 이상 확보 시 빈도 급감.",
        log_excerpt="ufs: bkops level 3 (critical)\nblk_mq: request latency 612ms on /dev/sda",
    ),
    # ---- Image Sensor ----
    FailureTemplate(
        family="Image Sensor",
        summary="[{chip}] 특정 조도에서 horizontal banding(가로 줄무늬) noise",
        symptom="실내 LED 조명(특정 PWM dimming 주파수) 환경에서 프리뷰에 가로 방향 banding이 나타납니다. 야외/자연광에서는 정상입니다.",
        repro=["LED PWM 조광 1kHz~2kHz 환경", "30fps 프리뷰", "AE 자동"],
        severity="Major",
        category="Firmware",
        root_cause="LED flicker 주파수와 sensor의 rolling shutter readout 주기가 beat를 형성. ISP FW의 anti-flicker(50/60Hz) 검출기가 비표준 PWM 주파수를 인식하지 못해 보정 미적용.",
        resolution="flicker 검출기를 고정 50/60Hz에서 FFT 기반 동적 주파수 추정으로 변경하고, 검출된 주파수에 맞춰 exposure를 정수배로 quantize.",
        workaround="수동으로 노출시간을 flicker 주기의 정수배로 고정 시 사라짐.",
        log_excerpt="isp: flicker_detect freq=0Hz (no 50/60 lock)\nstats: row_mean variance high (banding score 0.83)",
    ),
    FailureTemplate(
        family="Image Sensor",
        summary="[{chip}] HDR 모드에서 움직이는 피사체에 motion artifact(ghosting)",
        symptom="staggered HDR 합성 시 빠르게 움직이는 피사체 경계에 잔상/이중상이 발생합니다.",
        repro=["staggered HDR 3-exposure", "수평 이동 피사체 >2m/s", "고대비 장면"],
        severity="Minor",
        category="Firmware",
        root_cause="long/short exposure 간 시간차에서 발생하는 본질적 motion이며, deghosting weight map이 고대비 edge에서 과소평가됨. sensor의 line-interleaved HDR readout timing 보고값이 ISP 합성 모듈에 부정확하게 전달.",
        resolution="sensor가 ISP에 정확한 exposure midpoint timestamp를 embedded line으로 전달하도록 수정, deghosting weight를 edge gradient 기반으로 재튜닝.",
        workaround="HDR 비활성화 또는 in-sensor HDR(DCG) 모드 사용.",
        log_excerpt="hdr: merge ghost_map max=0.91\nsensor: emb_line exposure_mid ts drift 1.2ms",
    ),
    # ---- 5G Modem ----
    FailureTemplate(
        family="5G Modem",
        summary="[{chip}] NSA→SA 핸드오버 중 간헐적 RRC connection drop",
        symptom="특정 통신사 망에서 NSA에서 SA로 전환 시 약 2% 확률로 RRC 연결이 끊기고 데이터 세션이 재설정됩니다.",
        repro=["NSA→SA 전환 시나리오 1000회", "특정 vendor gNB", "이동 중(차량) 환경"],
        severity="Critical",
        category="Firmware",
        root_cause="SA 전환 시 modem FW가 NSA의 secondary cell 설정을 완전히 해제하기 전에 SA registration을 시작하여 timing 충돌. 특정 gNB의 비표준 reconfiguration 메시지 순서에서만 노출됨.",
        resolution="SCG release 완료 ACK를 기다린 후 SA registration을 진행하도록 state machine ordering 수정. 비표준 메시지 순서에 대한 robustness 처리 추가.",
        workaround="SA 모드 선호도를 낮추거나 NSA 고정 시 회피.",
        log_excerpt="RRC: scgFailureInformation triggered\nNAS: 5GMM registration retry (cause #22)",
    ),
    FailureTemplate(
        family="5G Modem",
        summary="[{chip}] mmWave 빔 트래킹 실패로 throughput 급락 후 회복 지연",
        symptom="mmWave 환경에서 단말 회전/가림(hand blockage) 후 throughput이 급락하고 정상 회복까지 수 초가 소요됩니다.",
        repro=["28GHz mmWave 셀", "hand blockage 반복", "회전 속도 다양"],
        severity="Major",
        category="Firmware",
        root_cause="blockage로 serving beam이 끊긴 뒤 beam failure recovery(BFR) 트리거 threshold가 보수적으로 설정되어 검출이 지연. 후보 beam set 갱신 주기도 과도하게 길었음.",
        resolution="BFR 트리거 L1-RSRP threshold와 hysteresis를 재튜닝, 후보 beam 측정 주기 단축. blockage 패턴 예측 기반 선제 beam switch 도입.",
        workaround="없음(성능 저하, 기능 정상).",
        log_excerpt="L1: beamFailureDetection counter=N\nMAC: BFR request sent, recovery 3.4s",
    ),
    # ---- Display Driver IC ----
    FailureTemplate(
        family="Display Driver IC",
        summary="[{chip}] 가변주사율(VRR) 전환 시 화면 깜빡임(flicker)",
        symptom="120Hz↔1Hz 가변주사율(LTPO) 전환 시 특정 회색조 패턴에서 눈에 띄는 brightness flicker가 발생합니다.",
        repro=["LTPO 1~120Hz 동적 전환", "중간 회색조(L40~L60) 패턴", "저휘도"],
        severity="Major",
        category="Timing",
        root_cause="주사율 전환 시 DDI의 gamma/Vcom 보정값이 새 refresh rate에 맞춰 업데이트되는 타이밍이 frame boundary와 정렬되지 않아 1프레임 동안 잘못된 Vcom 적용.",
        resolution="frame rate 전환과 Vcom/gamma register 업데이트를 vsync에 atomic하게 동기화하고, 전환 프레임에 대해 보간된 중간 보정값 적용.",
        workaround="VRR 비활성화(고정 60/120Hz) 시 미발생.",
        log_excerpt="ddi: rr_switch 120->1 at frame 8821\nddi: vcom update applied mid-frame (offset 0.3line)",
    ),
    FailureTemplate(
        family="Display Driver IC",
        summary="[{chip}] 폴더블 패널 접힘부 경계에서 색온도 불일치(color mura)",
        symptom="폴더블 패널을 펼친 상태에서 힌지 경계를 가로질러 좌우 색온도가 미세하게 다르게 보입니다.",
        repro=["펼침 상태 단색 화면(W/그레이)", "정면 시야", "공장 캘리브레이션 후"],
        severity="Minor",
        category="Hardware",
        root_cause="좌우 패널 구동을 담당하는 두 채널의 reference 전류 source가 process variation으로 미세하게 다르고, FW의 per-channel demura LUT가 힌지 경계 영역을 별도 보정하지 않음.",
        resolution="힌지 경계 zone에 대한 추가 demura calibration point를 도입하고, 채널 간 reference current를 trim으로 매칭.",
        workaround="없음(외관 품질 이슈).",
        log_excerpt="demura: zone boundary delta_u'v' = 0.006\nchannel ref_iref L=0x3F R=0x41",
    ),
    # ---- PMIC ----
    FailureTemplate(
        family="PMIC",
        summary="[{chip}] 부하 급변(load transient) 시 코어 레일 undershoot로 AP reset",
        symptom="CPU가 idle에서 max로 급격히 부하가 변할 때 코어 전압이 순간적으로 droop되어 드물게 AP가 reset됩니다.",
        repro=["dvfs min→max 급전환 stress", "최대 부하 step", "저온 조건"],
        severity="Critical",
        category="Power",
        root_cause="load transient 시 buck converter의 DVS slew와 부하 추종 응답이 부족하여 undershoot 발생. FW의 DVS transition에서 phase add 타이밍이 부하 step보다 늦음.",
        resolution="transient 시 자동 phase add를 활성화하고 compensation을 재튜닝, AP에 DVS 완료 전 부하 인가를 막는 handshake 추가.",
        workaround="DVS step 폭을 줄이거나 출력 커패시턴스 증설 시 완화.",
        log_excerpt="pmic: BUCK1 UV warning (Vout 0.62V < 0.65V)\npmcu: AP watchdog reset, cause=core_uv",
    ),
    FailureTemplate(
        family="PMIC",
        summary="[{chip}] I2C/SPMI 버스 통신 중 간헐적 NACK로 레일 설정 누락",
        symptom="시스템 부팅 시 드물게 PMIC가 SPMI 명령에 NACK를 반환하여 일부 레일이 기본값으로 남아 주변장치가 비정상 동작합니다.",
        repro=["빠른 연속 SPMI write 부팅 시퀀스", "버스에 다수 slave", "고온"],
        severity="Major",
        category="Signal Integrity",
        root_cause="SPMI master의 연속 write 사이 bus turnaround 시간이 PMIC 내부 register write 완료보다 짧아, busy 상태에서 명령이 도착하면 NACK. PMIC FW의 register write latency가 datasheet 값보다 큰 corner 존재.",
        resolution="register write 후 ready flag를 polling 가능하도록 status register 노출, busy 중 명령은 NACK 대신 clock stretch로 처리하도록 변경.",
        workaround="SPMI write 사이 지연 삽입 시 회피.",
        log_excerpt="spmi: NACK on addr 0x1A reg 0x40\nrail LDO7 stays at POR default 1.8V",
    ),
    # ---- NPU ----
    FailureTemplate(
        family="NPU",
        summary="[{chip}] 특정 양자화 모델에서 추론 결과 비결정성(non-deterministic) 발생",
        symptom="동일 입력/동일 INT8 모델인데도 NPU 추론 결과가 실행마다 미세하게 달라져 정확도 회귀 테스트가 실패합니다.",
        repro=["INT8 양자화 CNN 반복 추론", "multi-core NPU 분할 실행", "동일 입력 1000회"],
        severity="Major",
        category="Firmware",
        root_cause="여러 NPU core에 레이어가 분할될 때 partial sum 누적 순서가 스케줄러에 따라 달라지고, accumulator의 saturation/rounding 시점 차이로 INT 결과가 1 LSB 달라짐. 비결정적 core 할당이 근본 원인.",
        resolution="동일 그래프에 대해 deterministic core mapping과 고정 누적 순서를 보장하는 컴파일러 옵션 추가, accumulation rounding을 round-half-to-even으로 통일.",
        workaround="single-core 강제 실행 시 결정적이나 성능 저하.",
        log_excerpt="npu: layer split across core[0,1,2]\nverify: output max abs diff = 1 (INT8)",
    ),
    FailureTemplate(
        family="NPU",
        summary="[{chip}] 대형 모델 로드 시 DMA descriptor 부족으로 추론 실패",
        symptom="weight가 큰 트랜스포머 모델을 로드하면 NPU가 'descriptor exhausted' 에러를 반환하며 추론을 시작하지 못합니다.",
        repro=[">300MB weight 모델", "여러 모델 동시 상주", "fragmented 메모리 상태"],
        severity="Major",
        category="Firmware",
        root_cause="weight tiling이 메모리 단편화 상태에서 작은 tile로 과도하게 분할되어 DMA descriptor ring을 소진. FW가 descriptor 부족 시 동적 확장이나 tile 병합을 하지 않음.",
        resolution="descriptor ring 동적 확장과 인접 tile coalescing 추가, 로드 시 메모리 단편화 정도에 따라 tile 크기 적응.",
        workaround="모델 단독 로드 또는 디바이스 재시작 후 로드.",
        log_excerpt="npu: DMA descriptor ring exhausted (used 4096/4096)\nloader: weight tiled into 5120 tiles",
    ),
    # ---- Memory PHY ----
    FailureTemplate(
        family="Memory PHY",
        summary="[{chip}] 최고속(8533Mbps) 동작 시 특정 온도에서 비트 에러 상승",
        symptom="LPDDR5X를 8533Mbps로 동작시킬 때 약 70°C 부근에서 read DQ에 간헐적 비트 에러(ECC correctable 증가)가 발생합니다.",
        repro=["8533Mbps full speed", "70~80°C 구간 sweep", "memory stress(MATS)"],
        severity="Critical",
        category="Timing",
        root_cause="온도 상승에 따른 DRAM/PHY skew 변화를 주기적 training(eye re-centering)이 따라가지 못함. periodic read training 간격이 길고, 온도 보상(VTC) 계수가 corner DRAM에 부정확.",
        resolution="온도 변화율 기반 적응형 training 트리거 도입(고정 주기→이벤트 기반), VTC 계수를 device별 MR 정보로 보정.",
        workaround="속도를 7500Mbps로 낮추면 마진 확보.",
        log_excerpt="dramc: periodic_read_training interval 32ms\nedac: CE count rising at TDQS 0x3A, temp 72C",
    ),
    FailureTemplate(
        family="Memory PHY",
        summary="[{chip}] self-refresh 진입/탈출 시 드문 training 실패로 hang",
        symptom="저전력 self-refresh를 빈번히 진입/탈출하는 워크로드에서 매우 드물게 exit training이 실패하여 메모리 접근이 멈춥니다.",
        repro=["빈번한 SR enter/exit(아이들↔부하)", "최저전압 corner", "수십만 cycle 후"],
        severity="Major",
        category="Timing",
        root_cause="SR exit 후 ZQ calibration과 DQS gate training의 순서가 특정 corner에서 race를 일으켜 gate window를 놓침. exit latency tCKSRX 마진이 부족.",
        resolution="SR exit 시퀀스에서 ZQ cal 완료를 gate training 전에 보장하도록 ordering 고정, tCKSRX에 guard band 추가.",
        workaround="self-refresh 최소 체류시간을 늘리면 빈도 감소.",
        log_excerpt="dramc: SRX gate training fail, retry\nphy: dqs_gate window not found ch1 rank0",
    ),
    # ---- Automotive MCU ----
    FailureTemplate(
        family="Automotive MCU",
        summary="[{chip}] CAN-FD 고부하 버스트에서 간헐적 프레임 손실",
        symptom="CAN-FD 버스 부하가 80%를 초과하는 버스트 구간에서 드물게 수신 프레임이 누락되어 ADAS 센서 데이터가 stale 처리됩니다.",
        repro=["CAN-FD 5Mbps 데이터 페이즈", "버스 부하 >80% 버스트", "다중 ECU 동시 송신"],
        severity="Critical",
        category="Firmware",
        root_cause="수신 FIFO가 ISR 처리 지연 동안 overflow. 우선순위 역전으로 인해 CAN RX ISR보다 낮은 우선순위 태스크가 임계영역에서 인터럽트를 길게 마스킹.",
        resolution="RX FIFO watermark 인터럽트 도입 및 임계영역 마스킹 구간 최소화, RX 처리를 DMA 기반으로 오프로드.",
        workaround="버스 부하를 70% 이하로 설계하거나 메시지 주기 조정.",
        log_excerpt="can0: RX FIFO overflow (lost 3 frames)\nrtos: ISR latency 142us exceeds budget 80us",
    ),
    FailureTemplate(
        family="Automotive MCU",
        summary="[{chip}] ASIL-D lockstep 코어 간 transient 불일치로 false safety trap",
        symptom="고온/고부하 동작 중 매우 드물게 lockstep 코어 비교기가 불일치를 보고하여 안전 trap이 발생, 기능이 안전상태로 진입합니다.",
        repro=["lockstep 활성", "고온 125°C + 최대 부하", "장시간 동작"],
        severity="Blocker",
        category="Hardware",
        root_cause="두 코어 입력 클럭 사이 미세 skew와 고온에서의 셋업 마진 감소가 결합되어 비교 윈도우에서 transient glitch가 불일치로 검출됨. 실제 연산 오류는 아님(false positive).",
        resolution="lockstep 비교 윈도우에 delay matching 보정과 metastability filter 추가, 클럭 트리 skew를 재밸런싱.",
        workaround="없음. 안전 기능이므로 우회 불가, FW/clock 보정 필요.",
        log_excerpt="lockstep: compare mismatch addr=0x2008_1A40\nsafety: SW trap, enter safe state (no real fault)",
    ),
    # ---- Secure Element ----
    FailureTemplate(
        family="Secure Element",
        summary="[{chip}] NFC 동시 동작 중 eSE APDU 응답 timeout",
        symptom="NFC 카드 에뮬레이션과 eSE 접근이 동시에 일어나는 결제 시나리오에서 드물게 APDU 응답이 timeout되어 결제가 실패합니다.",
        repro=["NFC CE + eSE 접근 동시", "리더기 RF 필드 변동", "반복 탭"],
        severity="Critical",
        category="Firmware",
        root_cause="NFC와 eSE가 공유하는 통신 채널의 중재(arbitration)에서 RF 필드 급변 시 eSE 트랜잭션이 선점되어 응답 지연. wired-mode 전환 타이밍이 RF 이벤트와 충돌.",
        resolution="eSE 트랜잭션 진행 중에는 NFC RF 이벤트로 인한 채널 선점을 막는 lock을 추가하고, wired/contactless 전환에 hysteresis 적용.",
        workaround="없음(결제 신뢰성 이슈).",
        log_excerpt="ese: APDU SW=timeout (no 0x9000)\nnfc: RF field change during wired session",
    ),
    FailureTemplate(
        family="Secure Element",
        summary="[{chip}] 전압 glitch 환경에서 보안 부팅 측정값(attestation) 불일치",
        symptom="전원 노이즈가 심한 환경에서 드물게 secure boot의 measured boot 해시가 달라져 원격 attestation이 실패합니다.",
        repro=["전압 glitch injection(노이즈)", "반복 cold boot", "공급전압 하한"],
        severity="Major",
        category="Power",
        root_cause="부팅 측정 단계에서 전압 glitch가 측정 레지스터 업데이트 중 발생하면 부분 갱신된 값이 PCR에 반영됨. glitch 검출 회로의 응답이 측정 트랜잭션을 보호하지 못함.",
        resolution="측정 레지스터 업데이트를 glitch 검출과 연동된 atomic 트랜잭션으로 보호하고, glitch 감지 시 측정 단계를 재수행하도록 부트로더 수정.",
        workaround="전원 디커플링 보강 시 빈도 감소.",
        log_excerpt="sboot: PCR[2] extend mismatch\nglitch_det: VCC transient detected during measure",
    ),
]

# ---------------------------------------------------------------------------
# 부가 데이터 (고객, 환경)
# ---------------------------------------------------------------------------

CUSTOMERS = [
    "Anchor Mobile", "Northwind Devices", "Helios Automotive", "BluePeak Storage",
    "Orion Telecom", "Vega Imaging", "Meridian Robotics", "Cobalt Wearables",
    "Summit DataCenter", "Lumen Display", "Aster IoT", "Pioneer Drone",
]

REPORTER_NAMES = [
    "J. Park (FAE)", "S. Lee (Customer Eng)", "M. Choi (FAE)", "H. Kim (Support)",
    "D. Jung (FAE)", "Y. Han (Quality)", "T. Seo (Customer Eng)", "B. Yoon (FAE)",
]

SENIOR_ENGINEERS = [
    "senior.kang", "senior.lim", "senior.oh", "senior.shin", "senior.moon",
]

# 시니어 분석 코멘트 템플릿 (해결된 이슈에 부착, 주니어 학습용)
ANALYSIS_HEADER = "🔍 시니어 근본원인 분석"
DEBUG_METHOD_NOTES = [
    "재현 환경을 먼저 corner(온도/전압/속도)로 좁히는 것이 핵심이었습니다. 정상 케이스와의 차이를 1개 변수로 축소.",
    "로그에서 timing race를 의심: 두 비동기 경로의 ordering을 trace로 정렬해보면 원인이 드러납니다.",
    "datasheet 마진과 실측 corner를 비교. spec 위반이 아니라 corner stack-up이 원인인 경우가 많습니다.",
    "HW/FW 경계 신호를 logic analyzer로 캡처해 가설을 검증. 증상이 아닌 trigger 시점을 찾는 것이 중요.",
    "비결정성/간헐 이슈는 반드시 통계적으로(빈도, 조건) 특성화한 뒤 가설을 세웁니다.",
]


def severity_to_priority(sev: str) -> str:
    return {
        "Blocker": "Highest",
        "Critical": "High",
        "Major": "Medium",
        "Minor": "Low",
    }[sev]


@dataclass
class Issue:
    summary: str
    description: str
    issue_type: str
    priority: str
    labels: list[str]
    component: str
    status: str           # Open / In Progress / Resolved
    reporter_label: str
    customer: str
    chip: str
    fw_version: str
    severity: str
    category: str
    analysis_comment: Optional[str] = None  # 해결된 이슈에만
    meta: dict = field(default_factory=dict)


def _fw_version(prefix: str, rng: random.Random) -> str:
    return f"{prefix}.{rng.randint(1,5)}.{rng.randint(0,9)}.{rng.randint(100,999)}"


def _build_description(t: FailureTemplate, chip: str, fw: str, host: str,
                       customer: str, reporter: str, found_date: str) -> str:
    repro_lines = "\n".join(f"# {step}" for step in t.repro)
    return f"""h2. 고객 고장 보고

*고객사*: {customer}
*보고자*: {reporter}
*칩 모델*: {chip}
*펌웨어 버전*: {fw}
*호스트/플랫폼*: {host}
*발견일*: {found_date}
*고장 분류*: {t.category}
*심각도*: {t.severity}

h2. 증상 (Symptom)

{t.symptom}

h2. 재현 절차 (Reproduction)

{repro_lines}

h2. 로그 발췌 (Log Excerpt)

{{code}}
{t.log_excerpt}
{{code}}

h2. 영향 (Impact)

해당 고장은 *{t.severity}* 등급으로 분류됩니다. 고객 양산 일정 및 필드 신뢰성에 직접 영향을 줄 수 있어 우선 분석이 필요합니다.
"""


def _build_analysis_comment(t: FailureTemplate, engineer: str, method: str) -> str:
    return f"""{ANALYSIS_HEADER} (작성: {engineer})

*디버깅 접근*: {method}

*근본 원인 (Root Cause)*:
{t.root_cause}

*적용 해결책 (Resolution)*:
{t.resolution}

*임시 우회책 (Workaround)*:
{t.workaround}

----
_주니어 엔지니어 참고_: 이 케이스는 '{t.category}' 분류의 전형적 패턴입니다. 동일 분류의 다른 이슈에서도 위 디버깅 접근을 우선 적용해 보세요.
"""


def generate_issues(target_count: int = 50, seed: int = 20260608) -> list[Issue]:
    """현실적인 칩/펌웨어 고장 이슈를 target_count 건 생성한다."""
    rng = random.Random(seed)
    chip_by_family: dict[str, list[ChipLine]] = {}
    for c in CHIP_LINES:
        chip_by_family.setdefault(c.family, []).append(c)

    base_date = datetime(2026, 1, 6)
    issues: list[Issue] = []

    # 템플릿을 순회하며 변형을 만들어 target_count 도달
    idx = 0
    while len(issues) < target_count:
        t = FAILURE_TEMPLATES[idx % len(FAILURE_TEMPLATES)]
        variant = idx // len(FAILURE_TEMPLATES)  # 같은 템플릿 재사용 시 변형 번호
        idx += 1

        chip_line = rng.choice(chip_by_family[t.family])
        chip = chip_line.code
        fw = _fw_version(chip_line.fw_prefix, rng)
        host = rng.choice(chip_line.host_platforms)
        customer = rng.choice(CUSTOMERS)
        reporter = rng.choice(REPORTER_NAMES)
        found = base_date + timedelta(days=rng.randint(0, 150))
        found_date = found.strftime("%Y-%m-%d")

        # 상태 분포: 해결 50%, 진행중 25%, 오픈 25%
        roll = rng.random()
        if roll < 0.5:
            status = "Resolved"
        elif roll < 0.75:
            status = "In Progress"
        else:
            status = "Open"

        summary = t.summary.format(chip=chip)
        if variant > 0:
            # 변형 케이스는 고객/환경 차이를 제목에 반영해 중복 방지
            summary += f" ({customer} / {host})"

        description = _build_description(t, chip, fw, host, customer, reporter, found_date)

        labels = [
            t.category.replace(" ", "-"),
            chip_line.family.replace(" ", "-"),
            "customer-report",
            f"fw-{chip_line.fw_prefix}",
        ]

        analysis = None
        if status == "Resolved":
            engineer = rng.choice(SENIOR_ENGINEERS)
            method = rng.choice(DEBUG_METHOD_NOTES)
            analysis = _build_analysis_comment(t, engineer, method)

        issues.append(Issue(
            summary=summary,
            description=description,
            issue_type="Bug",
            priority=severity_to_priority(t.severity),
            labels=labels,
            component=chip_line.family,
            status=status,
            reporter_label=reporter,
            customer=customer,
            chip=chip,
            fw_version=fw,
            severity=t.severity,
            category=t.category,
            analysis_comment=analysis,
            meta={"host": host, "found_date": found_date},
        ))

    return issues


if __name__ == "__main__":
    items = generate_issues()
    by_status: dict[str, int] = {}
    by_cat: dict[str, int] = {}
    for it in items:
        by_status[it.status] = by_status.get(it.status, 0) + 1
        by_cat[it.category] = by_cat.get(it.category, 0) + 1
    print(f"총 {len(items)}건 생성")
    print("상태 분포:", by_status)
    print("분류 분포:", by_cat)
    print("\n--- 예시 1건 ---")
    print(items[0].summary)
    print(items[0].description[:400])
