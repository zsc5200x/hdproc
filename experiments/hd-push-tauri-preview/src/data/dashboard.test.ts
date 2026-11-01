import { describe, expect, it } from "vitest";
import { getDashboardSnapshot } from "./dashboard";

describe("dashboard gateway", () => {
  it("returns the same typed snapshot shape in browser fallback mode", async () => {
    const snapshot = await getDashboardSnapshot();

    expect(snapshot.metrics.ordersToday).toBe(186);
    expect(snapshot.orders).toHaveLength(6);
    expect(snapshot.orders[0]).toMatchObject({
      league: expect.any(String),
      match: expect.any(String),
      status: expect.stringMatching(/sent|failed|pending/),
    });
  });
});
