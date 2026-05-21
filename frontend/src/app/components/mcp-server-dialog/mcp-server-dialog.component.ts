import { Component, signal, computed, OnInit, inject, ViewChild, OnDestroy, DestroyRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatDialogRef, MAT_DIALOG_DATA, MatDialogModule } from '@angular/material/dialog';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import type { McpServer, McpServerCreate, McpServerUpdate, BuiltinServerTemplate } from '../../models';
import { ConfigSchemaFormComponent } from '../config-schema-form/config-schema-form.component';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { McpServerService } from '../../services/mcp-server.service';

interface DialogData {
  server?: McpServer;
  template?: BuiltinServerTemplate;
}

// MCP Server templates for quick configuration
export const MCP_TEMPLATES: Record<string, Record<string, unknown>> = {
  stdio: {
    transport: 'stdio',
    command: 'npx',
    args: ['-y', '@example/mcp-server']
  },
  sse: {
    transport: 'sse',
    url: 'http://localhost:3000/sse',
    headers: {
      Authorization: 'Bearer YOUR_TOKEN_HERE'
    }
  },
  'streamable-http': {
    transport: 'streamable-http',
    url: 'http://localhost:3000/mcp',
    headers: {
      Authorization: 'Bearer YOUR_TOKEN_HERE'
    }
  }
};

@Component({
  selector: 'app-mcp-server-dialog',
  standalone: true,
  imports: [CommonModule, FormsModule, MatDialogModule, MatSnackBarModule, ConfigSchemaFormComponent],
  templateUrl: './mcp-server-dialog.html',
  styleUrl: './mcp-server-dialog.scss'
})
export class McpServerDialogComponent implements OnInit, OnDestroy {
  @ViewChild('configSchemaForm') configSchemaForm?: ConfigSchemaFormComponent;

  protected readonly name = signal('');
  protected readonly description = signal('');
  protected readonly configJson = signal('');
  protected readonly isActive = signal(true);
  protected readonly error = signal<string | null>(null);
  protected readonly configJsonError = signal<string | null>(null);
  protected readonly saving = signal(false);
  protected readonly selectedTemplate = signal<string | null>(null);
  protected readonly testingConnection = signal(false);
  protected readonly testResult = signal<{ success: boolean; message: string } | null>(null);
  protected readonly templateTypes = ['stdio', 'sse', 'streamable-http'];

  // Schema form state
  protected readonly schemaFormValues = signal<Record<string, unknown>>({});
  protected readonly schemaFormValid = signal(false);

  private readonly dialogRef = inject(MatDialogRef<McpServerDialogComponent>);
  protected readonly data = inject<DialogData>(MAT_DIALOG_DATA);
  private readonly snackBar = inject(MatSnackBar);
  private readonly mcpServerService = inject(McpServerService);
  private readonly destroyRef = inject(DestroyRef);

  // Mode detection
  protected readonly isEditMode = computed(() => !!this.data?.server && !this.data?.server.is_builtin);
  protected readonly isBuiltinConfigureMode = computed(() => !!this.data?.server?.is_builtin);
  protected readonly isTemplateMode = computed(() => !!this.data?.template);
  protected readonly canTestConnection = computed(() => {
    return !this.testingConnection() && !this.configJsonError() && this.configJson().trim().length > 0;
  });

  // Convenience accessors
  protected get server(): McpServer | undefined {
    return this.data?.server;
  }

  protected get template(): BuiltinServerTemplate | undefined {
    return this.data?.template;
  }

  private handleError(context: string, err: unknown): void {
    this.saving.set(false);
    console.error(`Failed to ${context}:`, err);
    const message = (err as any)?.error?.detail || (err as any)?.message || `Failed to ${context}`;
    this.snackBar.open(message, 'Close', { duration: 5000, panelClass: 'error-snackbar' });
  }

  protected testConnection(): void {
    // Clear previous result
    this.testResult.set(null);

    // Validate JSON config
    const json = this.configJson().trim();
    if (!json) {
      this.testResult.set({ success: false, message: 'Configuration is empty' });
      return;
    }

    let parsedConfig: Record<string, unknown>;
    try {
      parsedConfig = JSON.parse(json);
    } catch {
      this.testResult.set({ success: false, message: 'Invalid JSON format' });
      return;
    }

    // Set loading state
    this.testingConnection.set(true);

    this.mcpServerService.testConnection(parsedConfig)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (response) => {
          this.testingConnection.set(false);
          // Use the detailed message from the BE response
          this.testResult.set({ success: response.success, message: response.message });
        },
        error: (err) => {
          this.testingConnection.set(false);
          const message = (err as any)?.error?.detail || (err as any)?.message || 'Connection test failed';
          this.testResult.set({ success: false, message });
        }
      });
  }

  ngOnInit(): void {
    if (this.data?.server && !this.data.server.is_builtin) {
      // Edit mode - pre-fill form
      const server = this.data.server;
      this.name.set(server.name);
      this.description.set(server.description || '');
      this.isActive.set(server.is_active);
      if (server.config && Object.keys(server.config).length > 0) {
        this.configJson.set(JSON.stringify(server.config, null, 2));
      }
    } else if (this.data?.server?.is_builtin) {
      // Builtin configure mode - initialize schema form with existing values
      const server = this.data.server;
      this.name.set(server.name);
      this.description.set(server.description || '');
      if (server.initial_values) {
        this.schemaFormValues.set({ ...server.initial_values });
      }
    }
  }

  ngOnDestroy(): void {
    // Cleanup handled by takeUntilDestroyed() operator
  }

  protected onNameChange(event: Event): void {
    const target = event.target as HTMLInputElement;
    this.name.set(target.value);
  }

  protected onDescriptionChange(event: Event): void {
    const target = event.target as HTMLTextAreaElement;
    this.description.set(target.value);
  }

  protected onConfigJsonChange(event: Event): void {
    const target = event.target as HTMLTextAreaElement;
    this.configJson.set(target.value);
    this.validateConfigJson();
    this.testResult.set(null);
  }

  protected onIsActiveChange(event: Event): void {
    const target = event.target as HTMLInputElement;
    this.isActive.set(target.checked);
  }

  protected selectTemplate(type: string): void {
    // If clicking the same template, just deselect (keep content)
    if (this.selectedTemplate() === type) {
      this.selectedTemplate.set(null);
      return;
    }

    // Apply new template
    const preset = MCP_TEMPLATES[type];
    if (preset) {
      this.configJson.set(JSON.stringify(preset, null, 2));
      this.selectedTemplate.set(type);
      this.validateConfigJson();
    }
  }

  protected formatJson(): void {
    const json = this.configJson().trim();
    if (!json) return;

    try {
      const parsed = JSON.parse(json);
      this.configJson.set(JSON.stringify(parsed, null, 2));
      this.validateConfigJson();
    } catch {
      // If JSON is invalid, don't format
    }
  }

  protected onConfigKeydown(event: KeyboardEvent): void {
    // Handle Tab key to insert 2 spaces instead of moving focus
    if (event.key === 'Tab') {
      event.preventDefault();
      const target = event.target as HTMLTextAreaElement;
      const start = target.selectionStart;
      const end = target.selectionEnd;
      const value = this.configJson();

      // Insert 2 spaces at cursor position
      const newValue = value.substring(0, start) + '  ' + value.substring(end);
      this.configJson.set(newValue);

      // Move cursor after the inserted spaces
      requestAnimationFrame(() => {
        target.selectionStart = target.selectionEnd = start + 2;
      });
    }
  }

  protected autoResize(event: Event): void {
    const target = event.target as HTMLTextAreaElement;
    // Reset height to auto to get correct scrollHeight
    target.style.height = 'auto';
    // Set height based on content (min 4 rows ~96px, max 12 rows ~288px)
    const minHeight = 96;
    const maxHeight = 288;
    const newHeight = Math.min(Math.max(target.scrollHeight, minHeight), maxHeight);
    target.style.height = `${newHeight}px`;
  }

  // Schema form output handlers
  protected onSchemaValuesChange(values: Record<string, unknown>): void {
    this.schemaFormValues.set(values);
  }

  protected onSchemaValidChange(isValid: boolean): void {
    this.schemaFormValid.set(isValid);
  }

  private validateConfigJson(): boolean {
    const json = this.configJson().trim();
    if (!json) {
      this.configJsonError.set(null);
      return true;
    }

    try {
      JSON.parse(json);
      this.configJsonError.set(null);
      return true;
    } catch {
      this.configJsonError.set('Invalid JSON format');
      return false;
    }
  }

  protected handleClose(): void {
    this.dialogRef.close(null);
  }

  protected handleSubmit(): void {
    this.error.set(null);

    if (this.isBuiltinConfigureMode()) {
      this.handleBuiltinConfigureSubmit();
    } else if (this.isTemplateMode()) {
      this.handleTemplateSubmit();
    } else {
      this.handleEditOrCreateSubmit();
    }
  }

  private handleBuiltinConfigureSubmit(): void {
    const server = this.server;
    if (!server) return;

    this.saving.set(true);
    this.mcpServerService.configureBuiltin({
      template_name: server.name,
      values: this.schemaFormValues()
    }).subscribe({
      next: (updatedServer) => {
        this.saving.set(false);
        this.mcpServerService.servers.update(servers =>
          servers.map(s => s.id === server.id ? updatedServer : s)
        );
        this.snackBar.open(`Configuration saved for "${server.name}"`, 'Close', {
          duration: 3000,
          panelClass: 'success-snackbar'
        });
        this.dialogRef.close({ type: 'builtin-update', server: updatedServer });
      },
      error: (err) => this.handleError('save builtin configuration', err)
    });
  }

  private handleTemplateSubmit(): void {
    const tmpl = this.template;
    if (!tmpl) return;

    this.saving.set(true);
    this.mcpServerService.configureBuiltin({
      template_name: tmpl.name,
      values: this.schemaFormValues()
    }).subscribe({
      next: (newServer) => {
        this.saving.set(false);
        this.snackBar.open(`Server "${newServer.name}" configured successfully`, 'Close', {
          duration: 3000,
          panelClass: 'success-snackbar'
        });
        this.dialogRef.close({ type: 'template-create', server: newServer });
      },
      error: (err) => this.handleError('configure template', err)
    });
  }

  private handleEditOrCreateSubmit(): void {
    // Validation - Name
    const nameValue = this.name().trim();
    if (!nameValue) {
      this.error.set('Name is required');
      return;
    }
    if (nameValue.length > 128) {
      this.error.set('Name must be 128 characters or less');
      return;
    }

    // Validate JSON config
    if (!this.validateConfigJson()) {
      return;
    }

    // Build result
    let config: Record<string, unknown> | undefined;
    const configJson = this.configJson().trim();
    if (configJson) {
      config = JSON.parse(configJson);
    }

    if (this.isEditMode() && this.server) {
      // Edit mode
      const update: McpServerUpdate = {
        name: nameValue,
        description: this.description().trim() || null,
        config,
        is_active: this.isActive()
      };
      this.dialogRef.close(update);
    } else {
      // Create mode
      const create: McpServerCreate = {
        name: nameValue,
        description: this.description().trim() || null,
        config,
        is_active: this.isActive()
      };
      this.dialogRef.close(create);
    }
  }

  protected handleResetToDefaults(): void {
    const server = this.server;
    if (!server || !this.isBuiltinConfigureMode()) return;

    if (!confirm('Reset configuration to defaults? Your custom settings will be lost.')) {
      return;
    }

    this.saving.set(true);
    this.mcpServerService.resetBuiltin(server.id).subscribe({
      next: (updatedServer) => {
        this.saving.set(false);
        this.snackBar.open(`Configuration reset to defaults for "${server.name}"`, 'Close', {
          duration: 3000,
          panelClass: 'success-snackbar'
        });
        // Re-initialize schema form with new initial values
        if (updatedServer.initial_values) {
          this.schemaFormValues.set({ ...updatedServer.initial_values });
          // Trigger the config schema form to re-initialize
          if (this.configSchemaForm) {
            this.configSchemaForm.resetForm();
          }
        }
      },
      error: (err) => this.handleError('reset builtin configuration', err)
    });
  }

  protected isSubmitDisabled(): boolean {
    if (this.saving()) {
      return true;
    }
    if (this.isBuiltinConfigureMode() || this.isTemplateMode()) {
      return !this.schemaFormValid();
    }
    return !this.name().trim() || this.configJsonError() !== null;
  }

  protected getDialogTitle(): string {
    if (this.isTemplateMode()) {
      const displayName = this.data.template?.display_name;
      return `Configure: ${displayName || this.template?.name || 'Template'}`;
    }
    if (this.isBuiltinConfigureMode()) {
      return `Configure: ${this.server?.name || 'Server'}`;
    }
    if (this.isEditMode()) {
      return `Edit: ${this.server?.name || 'Server'}`;
    }
    return 'Add MCP Server';
  }

  protected getSubmitButtonText(): string {
    if (this.isBuiltinConfigureMode() || this.isTemplateMode()) {
      return 'Save Configuration';
    }
    if (this.isEditMode()) {
      return 'Save';
    }
    return 'Add Server';
  }
}
