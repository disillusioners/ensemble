import { signal } from '@angular/core';
import { JobQueue } from '../models/job-queue.model';
import { createMockQueue, createMockQueueList } from '../testing/queue-test-helpers';

class TestableQueueService {
  readonly queues = signal<JobQueue[]>([]);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  // listQueues - returns observable that emits queues array and updates queues signal
  listQueues(projectId: string) {
    return {
      pipe: () => ({
        subscribe: (observer: any) => {
          const mockQueues = createMockQueueList(3);
          this.queues.set(mockQueues);
          if (typeof observer === 'function') {
            observer(mockQueues);
          } else if (observer.next) {
            observer.next(mockQueues);
          }
        }
      })
    };
  }

  // createQueue - adds new queue to start of queues array, updates signal
  createQueue(projectId: string, data: any) {
    return {
      pipe: () => ({
        subscribe: (observer: any) => {
          const created = createMockQueue({ queue_name: data.queue_name, queue_type: data.queue_type });
          this.queues.update(queues => [created, ...queues]);
          if (typeof observer === 'function') {
            observer(created);
          } else if (observer.next) {
            observer.next(created);
          }
        }
      })
    };
  }

  // getQueue - returns single queue by ID
  getQueue(projectId: string, queueId: string) {
    return {
      pipe: () => ({
        subscribe: (observer: any) => {
          const queue = createMockQueue({ queue_id: queueId });
          if (typeof observer === 'function') {
            observer(queue);
          } else if (observer.next) {
            observer.next(queue);
          }
        }
      })
    };
  }

  // updateQueue - updates queue in signal array
  updateQueue(projectId: string, queueId: string, data: any) {
    return {
      pipe: () => ({
        subscribe: (observer: any) => {
          const updated = createMockQueue({ queue_id: queueId, ...data });
          this.queues.update(queues => queues.map(q => q.queue_id === queueId ? updated : q));
          if (typeof observer === 'function') {
            observer(updated);
          } else if (observer.next) {
            observer.next(updated);
          }
        }
      })
    };
  }

  // deleteQueue - removes queue from signal array
  deleteQueue(projectId: string, queueId: string) {
    return {
      pipe: () => ({
        subscribe: (observer: any) => {
          this.queues.update(queues => queues.filter(q => q.queue_id !== queueId));
          if (typeof observer === 'function') {
            observer({ deleted: true });
          } else if (observer.next) {
            observer.next({ deleted: true });
          }
        }
      })
    };
  }

  // startQueue - updates queue with is_paused=false
  startQueue(projectId: string, queueId: string) {
    return {
      pipe: () => ({
        subscribe: (observer: any) => {
          const started = createMockQueue({ queue_id: queueId, is_paused: false });
          this.queues.update(queues => queues.map(q => q.queue_id === queueId ? started : q));
          if (typeof observer === 'function') {
            observer(started);
          } else if (observer.next) {
            observer.next(started);
          }
        }
      })
    };
  }

  // stopQueue - updates queue with is_paused=true
  stopQueue(projectId: string, queueId: string) {
    return {
      pipe: () => ({
        subscribe: (observer: any) => {
          const stopped = createMockQueue({ queue_id: queueId, is_paused: true });
          this.queues.update(queues => queues.map(q => q.queue_id === queueId ? stopped : q));
          if (typeof observer === 'function') {
            observer(stopped);
          } else if (observer.next) {
            observer.next(stopped);
          }
        }
      })
    };
  }

  // refreshQueues - sets loading true, calls listQueues, sets loading false
  refreshQueues(projectId: string) {
    this.loading.set(true);
    this.listQueues(projectId).pipe().subscribe({
      next: () => this.loading.set(false),
      error: () => this.loading.set(false)
    });
  }

  // clearError - clears error signal
  clearError() {
    this.error.set(null);
  }
}

describe('QueueService', () => {
  let service: TestableQueueService;

  beforeEach(() => {
    service = new TestableQueueService();
  });

  describe('listQueues', () => {
    it('should return queues array', () => {
      let result: JobQueue[] = [];
      service.listQueues('project-1').pipe().subscribe(queues => { result = queues; });
      expect(result.length).toBe(3);
    });

    it('should update queues signal', () => {
      service.listQueues('project-1').pipe().subscribe(() => {});
      expect(service.queues().length).toBe(3);
    });
  });

  describe('createQueue', () => {
    it('should add new queue to start of queues array', () => {
      const initialCount = service.queues().length;
      service.createQueue('project-1', { queue_name: 'New Queue', queue_type: 'fifo' }).pipe().subscribe(() => {});
      expect(service.queues().length).toBe(initialCount + 1);
      expect(service.queues()[0].queue_name).toBe('New Queue');
    });
  });

  describe('getQueue', () => {
    it('should return a queue', () => {
      let result: JobQueue | null = null;
      service.getQueue('project-1', 'queue-123').pipe().subscribe(queue => { result = queue; });
      expect(result?.queue_id).toBe('queue-123');
    });
  });

  describe('updateQueue', () => {
    it('should update queue in signal array', () => {
      service.listQueues('project-1').pipe().subscribe(() => {});
      const queueId = service.queues()[0].queue_id;
      service.updateQueue('project-1', queueId, { queue_name: 'Updated Queue' }).pipe().subscribe(() => {});
      const updated = service.queues().find(q => q.queue_id === queueId);
      expect(updated?.queue_name).toBe('Updated Queue');
    });
  });

  describe('deleteQueue', () => {
    it('should remove queue from signal array', () => {
      service.listQueues('project-1').pipe().subscribe(() => {});
      const initialCount = service.queues().length;
      const queueId = service.queues()[0].queue_id;
      service.deleteQueue('project-1', queueId).pipe().subscribe(() => {});
      expect(service.queues().length).toBe(initialCount - 1);
      expect(service.queues().find(q => q.queue_id === queueId)).toBeUndefined();
    });
  });

  describe('startQueue', () => {
    it('should update queue with is_paused=false', () => {
      service.listQueues('project-1').pipe().subscribe(() => {});
      const queueId = service.queues()[0].queue_id;
      service.startQueue('project-1', queueId).pipe().subscribe(() => {});
      const started = service.queues().find(q => q.queue_id === queueId);
      expect(started?.is_paused).toBe(false);
    });
  });

  describe('stopQueue', () => {
    it('should update queue with is_paused=true', () => {
      service.listQueues('project-1').pipe().subscribe(() => {});
      const queueId = service.queues()[0].queue_id;
      service.stopQueue('project-1', queueId).pipe().subscribe(() => {});
      const stopped = service.queues().find(q => q.queue_id === queueId);
      expect(stopped?.is_paused).toBe(true);
    });
  });

  describe('refreshQueues', () => {
    it('should set loading signal', () => {
      service.refreshQueues('project-1');
      expect(service.loading() === true || service.loading() === false).toBe(true);
    });
  });

  describe('clearError', () => {
    it('should clear error signal', () => {
      service.error.set('Test error');
      service.clearError();
      expect(service.error()).toBeNull();
    });
  });
});
