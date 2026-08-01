/** 공통 UI 프리미티브 — weekly-report-harness 의 디자인 언어를 그대로 따른다.
 *
 * 규칙(그 하네스와 동일):
 *   · 다크 전용 zinc 팔레트, 강조색은 sky
 *   · 카드 = rounded-xl border-zinc-800 bg-zinc-900/60
 *   · 배지 = rounded-full border, <색>-950/60 배경 + <색>-400 글자 + <색>-900/60 테두리
 *   · 페이지 제목은 그라데이션 배너 대신 제목 + 설명 한 줄
 *   · 포커스 링은 sky-500
 *
 * 화면마다 클래스를 흩뿌리지 않고 여기로 모아, 톤이 갈라지는 것을 막는다.
 *
 * 다크 테마에서 놓치기 쉬운 두 가지 — 실제로 글자가 보이지 않는 사고를 냈다:
 *   1. 마크다운은 `prose`만 쓰면 typography 기본색(어두운 회색)이 남는다.
 *      **반드시 `prose-invert`** 를 함께 붙인다.
 *   2. 폼 컨트롤(input/textarea/select)은 색을 상속하지 않는다 — UA 기본값이
 *      쓰인다. 아래 inputCls/selectCls 를 쓰고 직접 클래스를 짜지 않는다.
 */

import type { ButtonHTMLAttributes, ReactNode } from "react";

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "accent";

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  // 주 액션 (분석·저장·조회) — 흰색 채움
  primary: "bg-zinc-100 text-zinc-900 hover:bg-white",
  // 보조 액션 (초안·재시도·닫기) — 외곽선
  secondary: "border border-zinc-600 text-zinc-200 hover:bg-zinc-800",
  // 약한 액션 — 배경 없음
  ghost: "text-zinc-300 hover:bg-zinc-800",
  // 위험 (거부·삭제)
  danger: "border border-red-800/60 text-red-300 hover:bg-red-950/40",
  // 강조 (AI 생성·게시) — sky 계열
  accent: "border border-sky-500/50 bg-sky-500/10 text-sky-300 hover:bg-sky-500/20",
};

export function Button({
  variant = "primary", size = "md", className = "", ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant; size?: "sm" | "md" }) {
  const sz = size === "sm" ? "px-3 py-1.5 text-xs" : "px-5 py-2.5 text-sm";
  return (
    <button {...props}
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg font-medium outline-none transition focus-visible:ring-2 focus-visible:ring-sky-500 disabled:opacity-40 ${sz} ${BUTTON_VARIANTS[variant]} ${className}`} />
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`rounded-xl border border-zinc-800 bg-zinc-900/60 p-5 ${className}`}>{children}</div>;
}

export function PageHeader({ title, description, action }: {
  title: string; description?: string; action?: ReactNode;
}) {
  return (
    <header className="mb-8 flex items-start justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-zinc-50">{title}</h1>
        {description && <p className="mt-1.5 text-sm text-zinc-300">{description}</p>}
      </div>
      {action}
    </header>
  );
}

export function SectionTitle({ children, hint }: { children: ReactNode; hint?: ReactNode }) {
  return (
    <div className="mb-3 flex items-baseline gap-2">
      <h2 className="text-sm font-semibold tracking-tight text-zinc-200">{children}</h2>
      {hint && <span className="min-w-0 truncate text-[11px] text-zinc-400">{hint}</span>}
    </div>
  );
}

export function EmptyState({ message, icon = "◍", action }: {
  message: string; icon?: string; action?: ReactNode;
}) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 px-6 py-10 text-center">
      <p className="text-2xl text-zinc-600">{icon}</p>
      <p className="mt-2 text-sm text-zinc-400">{message}</p>
      {action && <div className="mt-3 flex justify-center">{action}</div>}
    </div>
  );
}

export function ErrorNote({ message, action }: { message: string; action?: ReactNode }) {
  return (
    <p className="rounded-lg border border-red-900/60 bg-red-950/40 px-4 py-3 text-sm text-red-400">
      {message}
      {action && <span className="ml-2">{action}</span>}
    </p>
  );
}

export function Tag({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <span title={title}
      className="inline-flex items-center rounded-md bg-zinc-800 px-2 py-0.5 text-[11px] text-zinc-300">
      {children}
    </span>
  );
}

export type BadgeTone = "neutral" | "sky" | "emerald" | "amber" | "red" | "violet";

const BADGE_TONES: Record<BadgeTone, string> = {
  neutral: "bg-zinc-800/80 text-zinc-300 border-zinc-700/60",
  sky: "bg-sky-950/60 text-sky-400 border-sky-900/60",
  emerald: "bg-emerald-950/60 text-emerald-400 border-emerald-900/60",
  amber: "bg-amber-950/60 text-amber-400 border-amber-900/60",
  red: "bg-red-950/60 text-red-400 border-red-900/60",
  violet: "bg-violet-950/60 text-violet-400 border-violet-900/60",
};

export function Badge({ children, tone = "neutral", title }: {
  children: ReactNode; tone?: BadgeTone; title?: string;
}) {
  return (
    <span title={title}
      className={`inline-flex items-center whitespace-nowrap rounded-full border px-2.5 py-0.5 text-[11px] font-medium ${BADGE_TONES[tone]}`}>
      {children}
    </span>
  );
}

/** 알림 — 심각도별 색. 큐 진입 결과처럼 "왜"를 전달해야 하는 곳에 쓴다. */
export function Notice({ tone, children }: { tone: "ok" | "info" | "warn"; children: ReactNode }) {
  const style = tone === "ok" ? "border-emerald-900/60 bg-emerald-950/40 text-emerald-400"
    : tone === "info" ? "border-sky-900/60 bg-sky-950/40 text-sky-400"
    : "border-amber-900/60 bg-amber-950/40 text-amber-400";
  const icon = tone === "ok" ? "✓" : tone === "info" ? "ℹ" : "⚠";
  return (
    <div className={`rounded-lg border px-2.5 py-1.5 text-xs leading-relaxed ${style}`}>
      {icon} {children}
    </div>
  );
}

/** 텍스트 입력 — 다크 배경에 맞춘 공통 스타일. */
export const inputCls =
  "w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 " +
  "placeholder:text-zinc-400 outline-none transition focus:border-sky-500/60 focus-visible:ring-1 focus-visible:ring-sky-500";

/** 셀렉트 — 입력과 같은 톤. */
export const selectCls =
  "rounded-md border border-zinc-700 bg-zinc-950 px-2 py-1 text-xs text-zinc-300 " +
  "outline-none focus:border-sky-500/60";
