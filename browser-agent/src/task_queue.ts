export class QueueFullError extends Error {}
export class QueueTimeoutError extends Error {}
export class QueueAbortedError extends Error {}

type QueuedTask = { start: () => void };

export class SerialTaskQueue {
  private running = false;
  private readonly pending: QueuedTask[] = [];

  constructor(private readonly maximumQueued: number, private readonly waitTimeoutMs: number) {}

  run<T>(operation: () => Promise<T>, signal?: AbortSignal): Promise<T> {
    if (signal?.aborted) return Promise.reject(new QueueAbortedError());
    if (this.running && this.pending.length >= this.maximumQueued) {
      return Promise.reject(new QueueFullError());
    }
    return new Promise<T>((resolve, reject) => {
      let timeout: ReturnType<typeof setTimeout> | undefined;
      const cleanup = (): void => {
        clearTimeout(timeout);
        signal?.removeEventListener("abort", abort);
      };
      const remove = (error: Error): void => {
        const index = this.pending.indexOf(task);
        if (index === -1) return;
        this.pending.splice(index, 1);
        cleanup();
        reject(error);
      };
      const abort = (): void => remove(new QueueAbortedError());
      const task: QueuedTask = {
        start: (): void => {
          cleanup();
          this.running = true;
          void (async () => {
            try {
              resolve(await operation());
            } catch (error) {
              reject(error);
            } finally {
              this.running = false;
              this.pending.shift()?.start();
            }
          })();
        },
      };
      if (!this.running) {
        task.start();
        return;
      }
      this.pending.push(task);
      signal?.addEventListener("abort", abort, { once: true });
      timeout = setTimeout(() => remove(new QueueTimeoutError()), this.waitTimeoutMs);
    });
  }
}
