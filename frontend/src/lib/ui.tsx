/**
 * The component vocabulary generated screens are written against.
 *
 * Deliberately quiet. The personality of a prototype comes from the design
 * direction and the brand tokens the user chose, not from this file -- if the
 * kit had a strong look of its own, every direction would arrive wearing it and
 * the choice between them would be cosmetic.
 *
 * Every component reads from CSS custom properties set on the frame root, so a
 * brand change repaints without recompiling a single screen.
 */
import * as React from "react";

type Div = React.HTMLAttributes<HTMLDivElement>;

function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

const BUTTON_VARIANTS: Record<string, string> = {
  primary: "text-white shadow-sm hover:opacity-90",
  secondary: "border bg-white hover:bg-slate-50",
  ghost: "hover:bg-slate-100",
  danger: "bg-red-600 text-white hover:bg-red-700",
};

const BUTTON_SIZES: Record<string, string> = {
  sm: "h-8 px-3 text-sm",
  md: "h-9 px-4 text-sm",
  lg: "h-11 px-6 text-base",
};

export function Button({
  variant = "primary",
  size = "md",
  className,
  style,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: string; size?: string }) {
  const branded = variant === "primary" ? { backgroundColor: "var(--brand-primary)" } : undefined;
  return (
    <button
      {...props}
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-[var(--brand-radius)] font-medium",
        "transition-colors focus-visible:outline-none focus-visible:ring-2",
        "focus-visible:ring-[var(--brand-primary)] focus-visible:ring-offset-2",
        "disabled:pointer-events-none disabled:opacity-50",
        BUTTON_VARIANTS[variant] ?? BUTTON_VARIANTS.primary,
        BUTTON_SIZES[size] ?? BUTTON_SIZES.md,
        className
      )}
      style={{ ...branded, ...style }}
    />
  );
}

export function Card({ className, ...props }: Div) {
  return (
    <div
      {...props}
      className={cx(
        "rounded-[var(--brand-radius)] border border-slate-200 bg-white shadow-sm",
        className
      )}
    />
  );
}

export function CardHeader({ className, ...props }: Div) {
  return <div {...props} className={cx("flex flex-col gap-1 p-5 pb-3", className)} />;
}

export function CardTitle({ className, ...props }: Div) {
  return (
    <h3
      {...props}
      className={cx("text-base font-semibold tracking-tight text-slate-900", className)}
      style={{ fontFamily: "var(--brand-font-heading)" }}
    />
  );
}

export function CardDescription({ className, ...props }: Div) {
  return <p {...props} className={cx("text-sm text-slate-500", className)} />;
}

export function CardContent({ className, ...props }: Div) {
  return <div {...props} className={cx("p-5 pt-0", className)} />;
}

export function CardFooter({ className, ...props }: Div) {
  return (
    <div
      {...props}
      className={cx("flex items-center gap-2 border-t border-slate-100 p-5", className)}
    />
  );
}

const FIELD =
  "w-full rounded-[var(--brand-radius)] border border-slate-300 bg-white px-3 py-2 text-sm " +
  "placeholder:text-slate-400 focus:border-[var(--brand-primary)] focus:outline-none " +
  "focus:ring-1 focus:ring-[var(--brand-primary)]";

export function Input({ className, ...props }: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cx(FIELD, "h-9", className)} />;
}

export function Textarea({
  className,
  ...props
}: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={cx(FIELD, "min-h-[80px]", className)} />;
}

export function Label({ className, ...props }: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return <label {...props} className={cx("text-sm font-medium text-slate-700", className)} />;
}

export function Select({
  options = [],
  className,
  children,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement> & {
  options?: Array<{ value: string; label: string }>;
}) {
  return (
    <select {...props} className={cx(FIELD, "h-9", className)}>
      {children ??
        options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
    </select>
  );
}

export function Checkbox({ className, ...props }: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      type="checkbox"
      className={cx("h-4 w-4 rounded border-slate-300 accent-[var(--brand-primary)]", className)}
    />
  );
}

export function Switch({
  checked,
  onChange,
  className,
}: {
  checked?: boolean;
  onChange?: React.ChangeEventHandler<HTMLInputElement>;
  className?: string;
}) {
  return (
    <label className={cx("inline-flex cursor-pointer items-center", className)}>
      <input type="checkbox" checked={checked} onChange={onChange} className="peer sr-only" />
      <span
        className="relative h-5 w-9 rounded-full bg-slate-300 transition-colors peer-checked:bg-[var(--brand-primary)]"
        aria-hidden
      >
        <span className="absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-white transition-transform peer-checked:translate-x-4" />
      </span>
    </label>
  );
}

const BADGE_VARIANTS: Record<string, string> = {
  default: "bg-slate-100 text-slate-700",
  success: "bg-emerald-50 text-emerald-700",
  warning: "bg-amber-50 text-amber-700",
  danger: "bg-red-50 text-red-700",
  accent: "text-white",
};

export function Badge({
  variant = "default",
  className,
  style,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & { variant?: string }) {
  return (
    <span
      {...props}
      className={cx(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
        BADGE_VARIANTS[variant] ?? BADGE_VARIANTS.default,
        className
      )}
      style={variant === "accent" ? { backgroundColor: "var(--brand-accent)", ...style } : style}
    />
  );
}

export function Table({ className, ...props }: React.TableHTMLAttributes<HTMLTableElement>) {
  return (
    <div className="w-full overflow-x-auto">
      <table {...props} className={cx("w-full border-collapse text-sm", className)} />
    </div>
  );
}

export function THead({ className, ...props }: Div) {
  return <thead {...props} className={cx("border-b border-slate-200", className)} />;
}

export function TBody({ className, ...props }: Div) {
  return <tbody {...props} className={cx("divide-y divide-slate-100", className)} />;
}

export function TR({ className, ...props }: React.HTMLAttributes<HTMLTableRowElement>) {
  return <tr {...props} className={cx("transition-colors hover:bg-slate-50", className)} />;
}

export function TH({ className, ...props }: React.ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      {...props}
      className={cx(
        "px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-500",
        className
      )}
    />
  );
}

export function TD({ className, ...props }: React.TdHTMLAttributes<HTMLTableCellElement>) {
  return <td {...props} className={cx("px-3 py-2.5 text-slate-700", className)} />;
}

export function Avatar({
  name = "",
  src,
  className,
}: {
  name?: string;
  src?: string;
  className?: string;
}) {
  const initials = name
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
  return (
    <span
      className={cx(
        "inline-flex h-8 w-8 items-center justify-center overflow-hidden rounded-full",
        "bg-slate-200 text-xs font-medium text-slate-600",
        className
      )}
    >
      {src ? <img src={src} alt={name} className="h-full w-full object-cover" /> : initials}
    </span>
  );
}

export function Separator({ className }: { className?: string }) {
  return <hr className={cx("border-slate-200", className)} />;
}

export function Tabs({
  tabs = [],
  active,
  onChange,
  className,
}: {
  tabs?: Array<{ key: string; label: string }>;
  active?: string;
  onChange?: (key: string) => void;
  className?: string;
}) {
  return (
    <div className={cx("flex gap-1 border-b border-slate-200", className)}>
      {tabs.map((tab) => {
        const selected = tab.key === active;
        return (
          <button
            key={tab.key}
            onClick={() => onChange?.(tab.key)}
            className={cx(
              "-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors",
              selected
                ? "border-[var(--brand-primary)] text-slate-900"
                : "border-transparent text-slate-500 hover:text-slate-800"
            )}
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}

export function Stat({
  label,
  value,
  hint,
  className,
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
  className?: string;
}) {
  return (
    <div
      className={cx(
        "rounded-[var(--brand-radius)] border border-slate-200 bg-white p-4",
        className
      )}
    >
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <p
        className="mt-1 text-2xl font-semibold text-slate-900"
        style={{ fontFamily: "var(--brand-font-heading)" }}
      >
        {value}
      </p>
      {hint ? <p className="mt-1 text-xs text-slate-500">{hint}</p> : null}
    </div>
  );
}

export function Empty({
  title,
  description,
  action,
  className,
}: {
  title: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cx(
        "flex flex-col items-center justify-center gap-2 rounded-[var(--brand-radius)]",
        "border border-dashed border-slate-300 px-6 py-12 text-center",
        className
      )}
    >
      <p className="text-sm font-medium text-slate-900">{title}</p>
      {description ? <p className="max-w-sm text-sm text-slate-500">{description}</p> : null}
      {action}
    </div>
  );
}
