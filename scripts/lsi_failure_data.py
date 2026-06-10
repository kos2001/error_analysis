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
    ChipLine(
        code="NFC-CTRL-N3",
        family="NFC Controller",
        desc_ko="NFC 컨트롤러 IC (NFC Forum NCI 2.3 준수)",
        fw_prefix="NFC3",
        host_platforms=["Android NFC stack (NCI 2.3)", "Linux neard host", "차량용 NFC 리더 모듈"],
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


def generate_issues(target_count: int = 50, seed: int = 20260608,
                    templates: list[FailureTemplate] | None = None,
                    base: datetime | None = None) -> list[Issue]:
    """현실적인 칩/펌웨어 고장 이슈를 target_count 건 생성한다.

    기본 인자는 기존 LSI 시드(결정론)와 동일한 출력을 유지한다 — 이미 Jira에
    push된 배치와의 중복 방지를 위해 새 배치는 templates/seed를 분리해서 쓸 것.
    """
    templates = templates or FAILURE_TEMPLATES
    rng = random.Random(seed)
    chip_by_family: dict[str, list[ChipLine]] = {}
    for c in CHIP_LINES:
        chip_by_family.setdefault(c.family, []).append(c)

    base_date = base or datetime(2026, 1, 6)
    issues: list[Issue] = []

    # 템플릿을 순회하며 변형을 만들어 target_count 도달
    idx = 0
    while len(issues) < target_count:
        t = templates[idx % len(templates)]
        variant = idx // len(templates)  # 같은 템플릿 재사용 시 변형 번호
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


# ---------------------------------------------------------------------------
# NFC 프로토콜 고장 템플릿 — NFC Forum 사양(https://nfc-forum.org/build/specifications) 기반
#   NCI v2.3 / Digital Protocol v2.4 / Activity v2.3 / Analog v3.0 / LLCP v1.4 /
#   Type 4 Tag v1.2 / Type 5 Tag v1.3 / TNEP v1.0 / WLC v2.0 / Connection Handover v1.5
# ---------------------------------------------------------------------------

NFC_FAILURE_TEMPLATES: list[FailureTemplate] = [
    FailureTemplate(
        family="NFC Controller",
        summary="[{chip}] NCI 데이터 흐름 정지 — CORE_CONN_CREDITS 미반환으로 HCI 전송 멈춤",
        symptom="대용량 NDEF 메시지 전송 중 NFCC가 credit(CORE_CONN_CREDITS_NTF)을 반환하지 않아 host의 데이터 패킷 전송이 영구 정지합니다. NCI v2.3 credit 기반 flow control 위반.",
        repro=["8KB NDEF 메시지를 Type 4 Tag에 write", "전송 중 RF field 순간 끊김(태그 이탈) 유발", "NCI 트레이스에서 credit 카운트 모니터링"],
        severity="Critical",
        category="Firmware",
        root_cause="RF 링크 단절 시 NFCC FW가 전송 중이던 패킷을 폐기하면서 해당 logical connection의 credit을 host에 반환하지 않음. NCI v2.3 §5.2의 'credit은 패킷 소비 시점에 반환' 규정을 단절 경로에서 누락. host 스택은 credit 0으로 무한 대기.",
        resolution="RF deactivation(RF_DEACTIVATE_NTF) 처리 경로에서 미반환 credit을 일괄 반환하도록 FW 수정. CORE_RESET 없이 회복 가능하게 connection별 credit 회계 재검증 로직 추가.",
        workaround="host에서 5초 무응답 시 CORE_RESET_CMD로 NFCC 재초기화.",
        log_excerpt="nci: tx blocked, conn_id=1 credits=0\nnci: RF_DEACTIVATE_NTF type=DISCOVERY\nnci: credit_ntf missing after deactivate (leak=3)",
    ),
    FailureTemplate(
        family="NFC Controller",
        summary="[{chip}] 다중 태그 환경에서 Type A anticollision 실패로 태그 인식 불가",
        symptom="NFC-A 태그 2장이 겹쳐 있으면 둘 다 인식하지 못합니다. 단일 태그는 정상. Digital Protocol v2.4의 SDD(Single Device Detection) 절차 문제로 보입니다.",
        repro=["Type 2 Tag 2장을 안테나 위 1cm 내 겹침", "polling loop 동작(Activity v2.3 기본 시퀀스)", "SDD_REQ CL1 응답 충돌 확인"],
        severity="Major",
        category="Firmware",
        root_cause="SDD 충돌 비트 위치 계산에서 anticollision frame의 비트 단위 정렬을 바이트 경계로 반올림하는 버그. Digital Protocol v2.4 §6.7.2의 bit-oriented anticollision을 byte 단위로만 구현하여 충돌 위치가 바이트 내부일 때 NVB 계산이 틀어짐.",
        resolution="SDD 루틴을 비트 단위 collision position 추적으로 재구현. NFC Forum DTA(Device Test Application) anticollision 시나리오 전 항목 통과 확인.",
        workaround="태그를 한 장씩 접촉하도록 안내. 리더 모드 retry 간격 단축으로 체감 개선.",
        log_excerpt="digital: SDD_REQ CL1 collision at bit 13\ndigital: NVB=0x20 (expected 0x15)\nactivity: poll A fail, restart discovery",
    ),
    FailureTemplate(
        family="NFC Controller",
        summary="[{chip}] ISO-DEP WTX 처리 누락으로 보안 APDU 교환 timeout",
        symptom="처리 시간이 긴 보안 애플릿(키 생성 등) APDU에서 카드가 S(WTX) 요청을 보내면 리더가 이를 무시하고 timeout 처리합니다. Type 4 Tag v1.2 / ISO-DEP 경로.",
        repro=["RSA 키쌍 생성 APDU (처리 2~3초)", "ISO-DEP FWT 초과 시 S(WTX) 발생 확인", "WTXM=10 요청 후 timeout 관찰"],
        severity="Critical",
        category="Firmware",
        root_cause="ISO-DEP 상태머신이 I-block 응답만 기다리는 상태에서 S(WTX) 수신 시 S(WTX) response를 보내지 않고 FWT 타이머를 그대로 유지. Digital Protocol v2.4 §16의 WTX 절차(요청된 WTXM만큼 FWT 연장 + 응답 송신) 미구현 경로 존재 — chaining 중에만 동작하고 단일 I-block 교환에서 누락.",
        resolution="S(WTX) 처리를 ISO-DEP 상태머신의 모든 대기 상태로 확장, FWT_INT = FWT × WTXM 연장 적용. DTA ISO-DEP WTX 시나리오 회귀 추가.",
        workaround="애플릿 측에서 장시간 연산을 분할(command chaining)하면 회피 가능.",
        log_excerpt="isodep: rx S(WTX) wtxm=10\nisodep: state=WAIT_IBLOCK, frame ignored\nisodep: FWT expired (77ms), deactivating",
    ),
    FailureTemplate(
        family="NFC Controller",
        summary="[{chip}] Type 5 Tag(NFC-V) 원거리에서 read 실패 — load modulation 진폭 미달",
        symptom="NFC-V(ISO 15693 기반) 태그를 2cm 이상에서 읽으면 실패율이 30%를 넘습니다. 경쟁사 리더는 같은 거리에서 정상. Analog v3.0 수신 감도 의심.",
        repro=["Type 5 Tag v1.3 레퍼런스 태그", "거리 2~4cm 스윕", "수신 진폭/복조 마진 측정"],
        severity="Major",
        category="Signal Integrity",
        root_cause="안테나 매칭 회로의 Q factor가 과도하게 높아 대역폭이 좁아짐 — 태그 부하변조 부반송파(423.75kHz)의 측대역이 감쇠되어 복조 마진 부족. Analog v3.0의 poller 수신 요구 레벨은 만족하나 마진이 1dB 미만으로 온도/개체 편차에 취약.",
        resolution="매칭 회로 Q를 낮추고(직렬 저항 조정) 수신 경로 AGC 임계 재튜닝. Analog v3.0 listener load modulation 최소 레벨 대비 수신 마진 6dB 확보를 출하 기준에 추가.",
        workaround="태그 밀착(1cm 이내) 사용 안내.",
        log_excerpt="analog: subcarrier amp 4.2mV (min margin <1dB)\nrf: t5t read retry 3/3 fail @3cm\nrf: CRC error burst on 423kHz sideband",
    ),
    FailureTemplate(
        family="NFC Controller",
        summary="[{chip}] LLCP 링크 유휴 시 SYMM 미송신으로 P2P 세션 조기 종료",
        symptom="P2P(LLCP v1.4) 연결 후 데이터가 잠시 없으면 1초 내 링크가 끊어집니다. SNEP로 큰 파일 수신 대기 중 발생.",
        repro=["LLCP 링크 수립(Connection Handover 협상 직후)", "1초 이상 무데이터 유지", "LTO(Link Timeout) 만료 관찰"],
        severity="Major",
        category="Firmware",
        root_cause="LLCP v1.4 §5.2 symmetry 절차 위반 — initiator FW가 보낼 PDU가 없을 때 SYMM PDU를 송신해야 하나, 송신 큐가 비면 타이머 스레드가 sleep으로 들어가 SYMM 주기(LTO의 1/2)를 놓침. target은 LTO 만료로 링크 해제.",
        resolution="SYMM 송신을 HW 타이머 기반으로 이전해 큐 상태와 무관하게 보장. LTO 협상값 검증 로직 추가.",
        workaround="LTO를 협상 가능한 최대(2.5s)로 설정하면 빈도 감소.",
        log_excerpt="llcp: tx queue empty, timer suspended\nllcp: peer LTO expired (500ms), DISC received\nsnep: GET fragmented transfer aborted at 34%",
    ),
    FailureTemplate(
        family="NFC Controller",
        summary="[{chip}] TNEP single response mode에서 NDEF write 후 상태 불일치",
        symptom="TNEP v1.0으로 IoT 기기 설정을 쓰면 간헐적으로 기기에 반영되지 않습니다. status message는 success인데 실제 서비스 데이터가 이전 값.",
        repro=["TNEP service select → NDEF write → status read", "T_WAIT 최소값 근처에서 반복", "Type 4 Tag 위 TNEP 서비스"],
        severity="Major",
        category="Firmware",
        root_cause="TNEP v1.0 §4.3의 T_WAIT 대기 후 status 확인 절차에서, 태그 측 FW가 NDEF 갱신 완료 전에 TNEP status를 success로 선반영. write 직후 RF 단절 시 flash commit이 유실되어도 status는 이미 success로 읽힘 — status 기록과 데이터 commit 간 ordering 부재.",
        resolution="서비스 데이터 flash commit 완료 후에만 TNEP status를 갱신하도록 순서 강제. commit 미완료 시 PROTOCOL_ERROR 반환.",
        workaround="write 후 read-back 검증을 클라이언트에 추가.",
        log_excerpt="tnep: svc=0x1A write 96B, status=SUCCESS\nflash: commit deferred (busy)\ntnep: read-back mismatch (old payload)",
    ),
    FailureTemplate(
        family="NFC Controller",
        summary="[{chip}] WLC 무선충전 중 NDEF 통신 병행 시 charging phase 이탈",
        symptom="WLC v2.0 무선충전 세션 중 listener가 NDEF 읽기를 시도하면 충전이 끊기고 재협상이 반복됩니다. 충전 효율이 60% 이하로 저하.",
        repro=["WLC poller 모드 충전 개시", "충전 중 WLC listener 정보(WLCCAP) 재읽기", "charging phase ↔ communication phase 전환 트레이스"],
        severity="Major",
        category="Power",
        root_cause="WLC v2.0의 charging phase와 communication phase 전환 시퀀스에서, poller FW가 field strength class 재협상 없이 통신용 저전계로 전환 후 복귀 시 이전 협상값을 잃고 기본 class로 떨어짐. 매 통신마다 capability 재협상이 발생하며 충전 듀티가 급감.",
        resolution="협상된 WLC field class를 세션 컨텍스트에 보존하고 phase 복귀 시 재사용. 재협상은 listener 요청 시에만 수행.",
        workaround="충전 중 태그 정보 폴링 주기를 30초 이상으로 설정.",
        log_excerpt="wlc: phase=COMM, field class drop 4->1\nwlc: renegotiate WLCCAP (count=27 in 60s)\nwlc: charge duty 41%",
    ),
    FailureTemplate(
        family="NFC Controller",
        summary="[{chip}] Connection Handover로 BT 페어링 시 OOB 데이터 불완전 전달",
        symptom="NFC 태그 터치로 블루투스 스피커 페어링(Connection Handover v1.5 + Bluetooth SSP v1.3) 시 일부 단말에서 페어링 창만 뜨고 연결이 실패합니다.",
        repro=["Handover Select 메시지에 BT carrier config 포함", "구형 host 스택(NDEF 단일 record 제한)과 조합", "OOB 데이터 길이 >255B 케이스"],
        severity="Minor",
        category="Firmware",
        root_cause="Handover Select Record의 alternative carrier에 첨부되는 Bluetooth OOB 데이터가 255B를 넘을 때 FW의 NDEF 조립기가 short record로 강제 인코딩하여 payload가 절단됨. NDEF 사양의 SR 플래그 판단을 carrier data 길이가 아닌 고정값으로 처리.",
        resolution="NDEF record 조립기에서 payload 길이 기반으로 SR 플래그를 동적 결정. Connection Handover v1.5 + BT SSP v1.3 조합 상호운용 시험 매트릭스 추가.",
        workaround="OOB에서 옵션 필드(LE role 등)를 제거해 255B 이하로 축소.",
        log_excerpt="ndef: record SR=1 but payload_len=312\nhandover: AC[0] carrier data truncated\nbt: OOB pairing failed (auth value mismatch)",
    ),
]


def generate_nfc_issues(target_count: int = 40, seed: int = 20260611) -> list[Issue]:
    """NFC 프로토콜 고장 배치 — 기존 LSI 배치(seed 20260608)와 분리된 결정론 시드."""
    return generate_issues(target_count=target_count, seed=seed,
                           templates=NFC_FAILURE_TEMPLATES, base=datetime(2026, 4, 1))


# ---------------------------------------------------------------------------
# NFC 프로토콜 고장 템플릿 2차 — 1차 배치(seed 20260611)가 이미 Jira에 push되어
# 결정론이 깨지지 않도록 별도 리스트/시드로 관리한다.
#   SNEP / Type 2 Tag v1.3 / Type 3 Tag v1.1(NFC-F) / Smart Poster RTD /
#   Activity v2.3 저전력 폴링 / NFC Authentication Protocol v1.0
# ---------------------------------------------------------------------------

NFC_FAILURE_TEMPLATES_V2: list[FailureTemplate] = [
    FailureTemplate(
        family="NFC Controller",
        summary="[{chip}] SNEP 대용량 PUT에서 fragment 재조립 실패로 전송 중단",
        symptom="SNEP(LLCP 상위)으로 1MB 이상 NDEF 메시지를 PUT하면 약 10% 확률로 수신측 재조립이 실패하고 REJECT가 반환됩니다.",
        repro=["SNEP PUT 1.5MB NDEF (사진 vCard)", "LLCP MIU 248B 협상 환경", "fragment 4000개 이상 케이스"],
        severity="Major",
        category="Firmware",
        root_cause="SNEP fragment 수신 버퍼를 고정 크기 ring으로 운용하는데, LLCP RR(수신 준비) 통지가 버퍼 회수보다 먼저 나가 producer가 ring을 덮어씀. SNEP의 'Continue 응답 후 나머지 fragment 연속 수신' 절차에서 흐름 제어가 LLCP credit과 이중으로 꼬임.",
        resolution="SNEP 수신 경로의 RR 통지를 버퍼 회수 완료 이후로 이동하고, ring full 시 LLCP busy(RNR) 전환. 1MB/8MB 장시간 PUT 회귀 추가.",
        workaround="송신측에서 MIU를 128B로 낮추면 재현율이 1% 미만으로 감소.",
        log_excerpt="snep: PUT len=1572864, continue sent\nllcp: RR issued while ring 98% full\nsnep: reassembly CRC mismatch at frag 3811, REJECT",
    ),
    FailureTemplate(
        family="NFC Controller",
        summary="[{chip}] Type 2 Tag 동적 메모리 write 중 전원 이탈 시 CC 영역 손상",
        symptom="Type 2 Tag v1.3 write 도중 태그가 안테나에서 이탈하면 드물게 태그의 CC(Capability Container)가 손상되어 이후 어떤 리더에서도 NDEF 태그로 인식되지 않습니다.",
        repro=["Type 2 Tag 144B 동적 영역 write", "write 중 태그 제거 반복 50회", "CC 블록(블록 3) 덤프 비교"],
        severity="Critical",
        category="Firmware",
        root_cause="write 시퀀스가 CC 블록을 마지막에 갱신하지 않고 NDEF TLV 길이 갱신과 같은 트랜잭션에서 선기록. RF 이탈 시 CC의 magic number 바이트만 기록되고 길이 필드가 미기록되어 비정합 상태로 잔류 — Type 2 Tag v1.3 §4.6 write 절차의 권고 순서 위반.",
        resolution="CC/길이 필드 갱신을 NDEF 데이터 기록 완료 후 단일 블록 write로 원자화. 손상 태그 복구용 진단 커맨드 추가.",
        workaround="write 완료 콜백 전 태그 유지 안내 UI 적용.",
        log_excerpt="t2t: write blk 4..39 ok\nrf: field off during blk 3 (CC) update\nt2t: CC magic=0xE1 len=0x00 (corrupt)",
    ),
    FailureTemplate(
        family="NFC Controller",
        summary="[{chip}] NFC-F(Type 3 Tag) 폴링 응답 타이밍 위반으로 교통카드 단말 호환 실패",
        symptom="일본향 교통 단말(JIS X 6319-4 계열)에서 SENSF_REQ 폴링에 대한 응답이 간헐적으로 누락되어 개찰 실패가 보고됩니다. Type 3 Tag v1.1 / NFC-F 경로.",
        repro=["SENSF_REQ TSN=0 폴링", "다른 RF 활동 직후 100ms 내 폴링 진입", "응답 슬롯 타이밍 측정"],
        severity="Critical",
        category="Timing",
        root_cause="NFC-F listen 모드 진입 시 RF 파라미터 재설정(아날로그 캘리브레이션)이 SENSF_REQ 첫 슬롯 타이밍(512/fc 이내 응답)과 겹침. Digital Protocol v2.4의 NFC-F 응답 슬롯 시간을 첫 폴링에서만 초과 — 이후 폴링은 정상이라 단말 retry 정책에 따라 증상이 갈림.",
        resolution="listen 모드 전환 시 캘리브레이션을 RF field 감지 시점으로 선행 이동, 첫 SENSF_REQ부터 슬롯 타이밍 보장. 단말 3종 상호운용 매트릭스 통과 확인.",
        workaround="단말 retry 2회 이상 설정 시 개찰 체감 정상.",
        log_excerpt="nfcf: SENSF_REQ rx, slot=0\nrf: analog cal in progress (1.2ms)\nnfcf: response missed slot deadline",
    ),
    FailureTemplate(
        family="NFC Controller",
        summary="[{chip}] Smart Poster RTD 파싱 오류 — 다중 레코드 NDEF에서 URI 누락",
        symptom="Smart Poster(URI+Text RTD 조합) 태그를 읽으면 제목만 표시되고 URL이 열리지 않습니다. 단일 URI 레코드 태그는 정상.",
        repro=["Smart Poster: Text(ko)+Text(en)+URI+Action 레코드", "ME/MB 플래그 조합 변형", "TNF=well-known, type='Sp'"],
        severity="Major",
        category="Firmware",
        root_cause="NDEF 파서가 Smart Poster payload 내부의 중첩 NDEF 메시지를 평탄화하면서 ME(Message End) 플래그를 외부 메시지 기준으로 검사 — 내부 메시지의 URI 레코드가 ME=0이면 미완성으로 판단해 폐기. Smart Poster RTD의 중첩 구조 처리 누락.",
        resolution="중첩 NDEF 파싱 컨텍스트를 분리해 내부/외부 ME 플래그를 독립 검사. NFC Forum 테스트 벡터(Sp 다중 레코드) 회귀 추가.",
        workaround="태그를 단일 URI 레코드로 재기록하면 동작.",
        log_excerpt="ndef: record Sp len=118, nested msg parse\nndef: inner URI rec dropped (ME=0)\napp: smart poster title only, uri=null",
    ),
    FailureTemplate(
        family="NFC Controller",
        summary="[{chip}] 저전력 폴링(LPCD) 오탐으로 대기 전류 3배 증가",
        symptom="주머니/금속 케이스 환경에서 저전력 카드 감지(LPCD)가 분당 수십 회 오탐하여 풀 폴링 루프(Activity v2.3)가 반복 기동, 대기 전류가 사양 대비 3배입니다.",
        repro=["금속 케이스 장착 후 화면 off 대기", "LPCD 임계 기본값", "전류 프로파일 24h 측정"],
        severity="Major",
        category="Power",
        root_cause="LPCD가 안테나 공진 진폭 변화만으로 카드 접근을 판정하는데, 금속 케이스로 기준 진폭 자체가 낮아져 온도 드리프트만으로 임계를 넘음. 오탐 시 Activity 폴링 루프 전체(NFC-A/B/F/V)를 도는 구조라 전류 소모가 큼.",
        resolution="LPCD 기준 진폭을 주기적 재캘리브레이션(temperature tracking)하고, 오탐 연속 감지 시 임계 자동 상향. 오탐 시 NFC-A 단일 기술 약식 폴링으로 1차 확인 후 풀 루프 진입.",
        workaround="설정에서 폴링 주기를 500ms→1s로 변경 시 전류 절반.",
        log_excerpt="lpcd: wake (amp delta 4.1%, thresh 4.0%)\nactivity: full poll A/B/F/V no target (x47/min)\npmu: idle current 2.9mA (spec 0.9mA)",
    ),
    FailureTemplate(
        family="NFC Controller",
        summary="[{chip}] NFC Authentication Protocol 보안 채널 수립 실패 — nonce 재사용 거부",
        symptom="NFC Authentication Protocol v1.0 기반 정품 인증 태그와 보안 채널을 맺을 때 두 번째 세션부터 인증이 거부됩니다. 첫 세션은 항상 성공.",
        repro=["NAP 보안 채널 수립 → 정상 종료", "동일 태그 재태깅", "두 번째 challenge 교환 캡처"],
        severity="Major",
        category="Security",
        root_cause="FW의 난수 생성기가 세션 간 DRBG 상태를 재시드 없이 재사용하면서 RF 재초기화 경로에서 카운터가 리셋 — 동일 nonce가 재발급되어 태그 측 재전송 공격 방지 로직이 인증을 거부. 첫 세션만 부팅 엔트로피로 성공.",
        resolution="RF deactivation마다 DRBG reseed(아날로그 노이즈 엔트로피 주입)를 강제하고 nonce 단조성 카운터를 비휘발 영역에 유지.",
        workaround="NFC 기능 off/on 시 임시 회복(부팅 엔트로피 재주입).",
        log_excerpt="nap: session#2 challenge nonce=0x7A21..(dup)\ntag: auth response SW=6985 (replay suspected)\ndrbg: reseed_counter=0 after rf reinit",
    ),
]


def generate_nfc_v2_issues(target_count: int = 24, seed: int = 20260612) -> list[Issue]:
    """NFC 프로토콜 2차 배치 — 1차(seed 20260611)와 분리된 결정론 시드."""
    return generate_issues(target_count=target_count, seed=seed,
                           templates=NFC_FAILURE_TEMPLATES_V2, base=datetime(2026, 5, 1))


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
