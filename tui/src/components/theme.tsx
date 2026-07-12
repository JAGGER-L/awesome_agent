import { createContext, useContext, type ReactNode } from "react";

export interface ThemeRoles {
  readonly accent: string;
  readonly brand: string;
  readonly assistant: string;
  readonly error: string;
  readonly muted: string;
  readonly user: string;
  readonly warning: string;
}

const defaultTheme: ThemeRoles = {
  accent: "cyan",
  brand: "greenBright",
  assistant: "white",
  error: "red",
  muted: "gray",
  user: "green",
  warning: "yellow",
};

const ThemeContext = createContext(defaultTheme);

export function ThemeProvider({
  children,
  value = defaultTheme,
}: {
  children: ReactNode;
  value?: ThemeRoles;
}) {
  return (
    <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
  );
}

export function useTheme(): ThemeRoles {
  return useContext(ThemeContext);
}
