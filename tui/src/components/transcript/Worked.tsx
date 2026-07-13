import { Box } from "ink";

import { ActivityLine } from "../activity/ActivityLine.js";

export function Worked({ durationMs }: { readonly durationMs: number }) {
  return (
    <Box marginTop={1}>
      <ActivityLine
        state="completed"
        marker="✻"
        text="Worked for"
        durationMs={durationMs}
        shimmer={false}
      />
    </Box>
  );
}
