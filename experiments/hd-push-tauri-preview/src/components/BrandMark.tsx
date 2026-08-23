export function BrandMark({ compact = false }: { compact?: boolean }) {
  return (
    <div className={compact ? "brand-mark brand-mark--compact" : "brand-mark"} aria-hidden="true">
      <span className="brand-mark__left" />
      <span className="brand-mark__right" />
      <span className="brand-mark__bar" />
    </div>
  );
}
