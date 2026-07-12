import { createContext, useContext, type ReactNode } from "react";

import {
  resolveTheme,
  type SemanticThemeRoles,
  type Theme,
} from "../preferences/theme.js";

export type ThemeRoles = SemanticThemeRoles;

const defaultTheme = resolveTheme("system", "ansi16");

const ThemeContext = createContext(defaultTheme);

export function ThemeProvider({
  children,
  value = defaultTheme,
}: {
  children: ReactNode;
  value?: Theme;
}) {
  return (
    <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
  );
}

export function useTheme(): Theme {
  return useContext(ThemeContext);
}
