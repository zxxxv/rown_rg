/** 같은 이름 프리셋 충돌 안내 상자 - SavePresetDialog·PresetEditorDialog가 함께 쓴다.
 * 덮어쓰기 버튼은 각 다이얼로그 푸터에 남는다(서로 다른 mutation 상태를 물기 때문). */
export function DuplicateNameNotice({ name }: { name: string }) {
  return (
    <div className="rounded border border-border bg-bg-secondary p-3 text-xs">
      <p className="font-medium text-fg-primary">같은 이름의 프리셋이 이미 있습니다: {name}</p>
      <p className="mt-0.5 text-fg-tertiary">
        덮어쓰면 기존 프리셋이 이 구성으로 바뀝니다. 따로 두려면 이름을 고쳐 주세요.
      </p>
    </div>
  );
}
