export class CoreSpawnError extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "CoreSpawnError";
  }
}

export class CoreShutdownError extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "CoreShutdownError";
  }
}

export class CoreTerminationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "CoreTerminationError";
  }
}
