import { describe, expect, it } from "vitest";

import {
  StoreInvariantError,
  createSurfaceStore,
} from "../../src/state/store.js";

describe("SurfaceStore", () => {
  it("dispatches synchronously and permits unsubscribe during notification", () => {
    const store = createSurfaceStore();
    const calls: string[] = [];
    const unsubscribe = store.subscribe(() => {
      calls.push("first");
      unsubscribe();
    });
    store.subscribe(() => calls.push("second"));
    store.dispatch({ type: "connection.start" });
    store.dispatch({ type: "connection.handshaking" });
    expect(calls).toEqual(["first", "second", "second"]);
  });

  it("rejects nested dispatch", () => {
    const store = createSurfaceStore();
    store.subscribe(() => store.dispatch({ type: "connection.handshaking" }));
    expect(() => store.dispatch({ type: "connection.start" })).toThrow(
      StoreInvariantError,
    );
  });
});
