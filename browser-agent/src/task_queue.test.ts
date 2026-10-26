import assert from "node:assert/strict";
import { test } from "node:test";

import { QueueAbortedError, QueueFullError, QueueTimeoutError, SerialTaskQueue } from "./task_queue.js";

test("rejects excess admissions without starting them", async () => {
  const queue = new SerialTaskQueue(1, 1000);
  let release!: () => void;
  const first = queue.run(() => new Promise<void>((resolve) => { release = resolve; }));
  const second = queue.run(async () => "second");
  await assert.rejects(queue.run(async () => "excess"), QueueFullError);
  release();
  await first;
  assert.equal(await second, "second");
});

test("removes canceled queued work and reuses its slot", async () => {
  const queue = new SerialTaskQueue(1, 1000);
  let release!: () => void;
  const first = queue.run(() => new Promise<void>((resolve) => { release = resolve; }));
  const controller = new AbortController();
  let canceledStarted = false;
  const canceled = queue.run(async () => { canceledStarted = true; }, controller.signal);
  controller.abort();
  await assert.rejects(canceled, QueueAbortedError);
  const replacement = queue.run(async () => "replacement");
  release();
  await first;
  assert.equal(await replacement, "replacement");
  assert.equal(canceledStarted, false);
});

test("queue wait has a deadline and timed out operations never start", async () => {
  const queue = new SerialTaskQueue(1, 10);
  let release!: () => void;
  const first = queue.run(() => new Promise<void>((resolve) => { release = resolve; }));
  let expiredStarted = false;
  await assert.rejects(queue.run(async () => { expiredStarted = true; }), QueueTimeoutError);
  release();
  await first;
  assert.equal(expiredStarted, false);
  assert.equal(await queue.run(async () => "healthy"), "healthy");
});

test("a failed running task releases the execution slot", async () => {
  const queue = new SerialTaskQueue(1, 1000);
  const first = queue.run(async () => { throw new Error("failed"); });
  const second = queue.run(async () => "second");
  await assert.rejects(first, /failed/);
  assert.equal(await second, "second");
});
