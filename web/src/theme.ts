/** 화면 전체가 공유하는 색 상수.
 *
 * 컴포넌트 파일에서 상수를 함께 export 하면 Vite fast refresh 가 깨지므로 분리했다.
 */

/** 카테고리·계열 구분용 순환 팔레트 (대시보드 차트). */
export const PALETTE = [
  "#38bdf8", "#a78bfa", "#34d399", "#fbbf24", "#f87171",
  "#22d3ee", "#c084fc", "#fb923c", "#a1a1aa", "#f472b6",
];

/** 이슈 상태 → 색. 관계 그래프 노드와 목록 배지가 같은 색을 쓴다. */
export const STATUS_HEX: Record<string, string> = {
  "진행 중": "#34d399", "해야 할 일": "#71717a", "완료": "#38bdf8",
};
