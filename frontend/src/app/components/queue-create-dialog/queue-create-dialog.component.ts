import { Component, inject, signal, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ReactiveFormsModule, FormBuilder, FormGroup, Validators, AbstractControl, ValidationErrors } from '@angular/forms';
import { MatDialogRef, MAT_DIALOG_DATA, MatDialogModule } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';

import { QueueType } from '../../models/job-queue.model';

// Reserved system queue names that cannot be used
const RESERVED_QUEUE_NAMES = ['system_fifo_queue', 'system_parallel_queue', 'system_kb_fifo_queue', 'system_defer_queue', 'system_background_queue'];

/**
 * Custom validator to check for reserved queue names
 */
function reservedQueueNameValidator(control: AbstractControl): ValidationErrors | null {
  if (!control.value) return null;
  const trimmedValue = control.value.trim().toLowerCase();
  if (RESERVED_QUEUE_NAMES.includes(trimmedValue)) {
    return { reservedName: true };
  }
  return null;
}

export interface QueueCreateDialogData {
  projectId: string;
}

export interface QueueCreateDialogResult {
  queue_name: string;
  queue_type: QueueType;
  concurrency_limit: number;
  description?: string;
}

@Component({
  selector: 'app-queue-create-dialog',
  standalone: true,
  imports: [
    CommonModule,
    ReactiveFormsModule,
    MatDialogModule,
    MatButtonModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
    MatSnackBarModule
  ],
  templateUrl: './queue-create-dialog.html',
  styleUrl: './queue-create-dialog.scss'
})
export class QueueCreateDialogComponent implements OnInit {
  private readonly fb = inject(FormBuilder);
  private readonly snackBar = inject(MatSnackBar);
  
  protected readonly dialogRef = inject(MatDialogRef<QueueCreateDialogComponent>);
  protected readonly data = inject<QueueCreateDialogData>(MAT_DIALOG_DATA);

  protected readonly isLoading = signal(false);
  protected readonly reservedNames = RESERVED_QUEUE_NAMES;

  protected readonly queueTypes = [
    { value: 'fifo', label: 'FIFO (First In, First Out)' },
    { value: 'parallel', label: 'Parallel (Concurrent execution)' },
    { value: 'defer', label: 'Defer (Background execution)' },
    { value: 'background', label: 'Background (Wait for all projects idle)' }
  ];

  protected readonly form: FormGroup = this.fb.group({
    queue_name: ['', [
      Validators.required, 
      Validators.minLength(1), 
      Validators.maxLength(100),
      reservedQueueNameValidator
    ]],
    queue_type: ['fifo', Validators.required],
    concurrency_limit: [1, [Validators.required, Validators.min(1), Validators.max(20)]],
    description: ['', Validators.maxLength(500)]
  });

  ngOnInit(): void {
    // Listen to queue_type changes to update concurrency validation
    this.form.get('queue_type')?.valueChanges.subscribe((type) => {
      if (type === 'fifo' || type === 'defer' || type === 'background') {
        // FIFO, Defer, and Background always use concurrency of 1
        this.form.get('concurrency_limit')?.setValue(1);
        this.form.get('concurrency_limit')?.disable();
      } else {
        // Parallel queues can use any concurrency from 1-20
        this.form.get('concurrency_limit')?.enable();
      }
    });

    // Initialize concurrency_limit as disabled for fifo
    this.form.get('concurrency_limit')?.disable();
  }

  protected get selectedQueueType(): string {
    return this.form.get('queue_type')?.value || 'fifo';
  }

  protected isConcurrencyDisabled(): boolean {
    return this.selectedQueueType === 'fifo' || this.selectedQueueType === 'defer' || this.selectedQueueType === 'background';
  }

  protected handleClose(): void {
    this.dialogRef.close();
  }

  protected handleSubmit(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }

    this.isLoading.set(true);

    try {
      const formValue = this.form.getRawValue();
      
      const result: QueueCreateDialogResult = {
        queue_name: formValue.queue_name!.trim(),
        queue_type: formValue.queue_type as QueueType,
        concurrency_limit: (formValue.queue_type === 'fifo' || formValue.queue_type === 'defer' || formValue.queue_type === 'background') ? 1 : formValue.concurrency_limit!,
        description: formValue.description?.trim() || undefined
      };

      this.dialogRef.close(result);
    } catch (err) {
      console.error('Failed to create queue:', err);
      this.snackBar.open(
        err instanceof Error ? err.message : 'Failed to create queue',
        'Close',
        {
          duration: 5000,
          panelClass: 'error-snackbar'
        }
      );
    } finally {
      this.isLoading.set(false);
    }
  }

  protected isSubmitDisabled(): boolean {
    return this.isLoading() || this.form.invalid;
  }
}
