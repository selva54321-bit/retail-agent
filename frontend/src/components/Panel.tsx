import type { PropsWithChildren, ReactNode } from 'react';

interface PanelProps extends PropsWithChildren {
  title?: string;
  subtitle?: string;
  className?: string;
  rightSlot?: ReactNode;
}

export function Panel({ title, subtitle, className = '', rightSlot, children }: PanelProps) {
  return (
    <section className={`panel ${className}`.trim()}>
      {(title || subtitle || rightSlot) && (
        <header className="panel-head">
          <div>
            {title && <h3>{title}</h3>}
            {subtitle && <p>{subtitle}</p>}
          </div>
          {rightSlot ? <div>{rightSlot}</div> : null}
        </header>
      )}
      {children}
    </section>
  );
}
