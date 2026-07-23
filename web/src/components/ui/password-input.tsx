import { Eye, EyeOff } from "lucide-react";
import { type ComponentPropsWithoutRef, forwardRef, useState } from "react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export type PasswordInputProps = Omit<ComponentPropsWithoutRef<typeof Input>, "type">;

/** 보여주기/숨기기 토글이 달린 비밀번호 입력. type 외 모든 props는 Input에 전달된다. */
export const PasswordInput = forwardRef<HTMLInputElement, PasswordInputProps>(
  function PasswordInput({ className, disabled, ...props }, ref) {
    const [show, setShow] = useState(false);
    return (
      <div className="relative">
        <Input
          ref={ref}
          type={show ? "text" : "password"}
          className={cn("pr-10", className)}
          disabled={disabled}
          {...props}
        />
        <button
          type="button"
          onClick={() => setShow((v) => !v)}
          disabled={disabled}
          className="absolute inset-y-0 right-0 flex w-10 items-center justify-center text-fg-tertiary hover:text-fg disabled:opacity-50"
          aria-label={show ? "비밀번호 숨기기" : "비밀번호 보여주기"}
          aria-pressed={show}
        >
          {show ? (
            <EyeOff aria-hidden className="h-4 w-4" />
          ) : (
            <Eye aria-hidden className="h-4 w-4" />
          )}
        </button>
      </div>
    );
  },
);
