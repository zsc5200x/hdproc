import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "./App";

describe("HD 推送终端", () => {
  it("加载仪表盘并展示核心推送指标", async () => {
    render(<App />);

    expect(await screen.findByRole("heading", { level: 1, name: "推送总览" })).toBeInTheDocument();
    expect(screen.getByText("今日订单")).toBeInTheDocument();
    expect(screen.getByText("推送成功率")).toBeInTheDocument();
    expect(screen.getByText("98.7%")).toBeInTheDocument();
  });

  it("可以按失败状态筛选订单并打开详情", async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByText("实时订单流");
    await user.click(screen.getByRole("button", { name: "失败" }));

    expect(screen.getByText("巴黎圣日耳曼 vs 里昂")).toBeInTheDocument();
    expect(screen.queryByText("阿森纳 vs 切尔西")).not.toBeInTheDocument();

    await user.click(screen.getByText("巴黎圣日耳曼 vs 里昂"));
    expect(screen.getByRole("dialog", { name: "订单详情" })).toBeInTheDocument();
    expect(screen.getByText("TG 推送记录")).toBeInTheDocument();
  });

  it("可以暂停并恢复推送引擎", async () => {
    const user = userEvent.setup();
    render(<App />);

    const toggle = await screen.findByRole("switch", { name: "推送引擎" });
    expect(toggle).toHaveAttribute("aria-checked", "true");

    await user.click(toggle);
    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "false"));
    expect(screen.getByText("推送已暂停")).toBeInTheDocument();
  });
});
