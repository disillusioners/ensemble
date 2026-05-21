import { signal, computed } from '@angular/core';
import type { McpServer, McpServerCreate, McpServerUpdate, BuiltinServerTemplate, ConfigSchemaField } from '../../models';

// MCP Server templates (mirrors component)
const MCP_TEMPLATES: Record<string, Record<string, unknown>> = {
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

// Mock MatSnackBar
class MockMatSnackBar {
  static lastOpen: { message: string; action?: string; options?: object } | null = null;

  open(message: string, action?: string, options?: { duration?: number; panelClass?: string }): void {
    MockMatSnackBar.lastOpen = { message, action, options };
  }

  static reset(): void {
    MockMatSnackBar.lastOpen = null;
  }
}

// Mock MatDialogRef
class MockMatDialogRef<T = unknown> {
  private closeFn: ((result?: T) => void) | null = null;

  close(result?: T): void {
    if (this.closeFn) {
      this.closeFn(result);
    }
  }

  setCloseHandler(fn: (result?: T) => void): void {
    this.closeFn = fn;
  }
}

// Dialog data interface (mirrors actual component)
interface DialogData {
  server?: McpServer;
  template?: BuiltinServerTemplate;
}

// Testable McpServerDialogComponent (mirrors actual component)
class TestableMcpServerDialogComponent {
  protected readonly name = signal('');
  protected readonly description = signal('');
  protected readonly configJson = signal('');
  protected readonly isActive = signal(true);
  protected readonly error = signal<string | null>(null);
  protected readonly configJsonError = signal<string | null>(null);
  protected readonly saving = signal(false);
  protected readonly selectedTemplate = signal<string | null>(null);

  // Schema form state
  protected readonly schemaFormValues = signal<Record<string, unknown>>({});
  protected readonly schemaFormValid = signal(false);

  private readonly dialogRef: MockMatDialogRef<McpServerCreate | McpServerUpdate | null>;
  protected readonly data: DialogData | null;
  protected readonly snackBar = new MockMatSnackBar();

  protected readonly isEditMode: () => boolean;
  protected readonly isBuiltinConfigureMode: () => boolean;
  protected readonly isTemplateMode: () => boolean;

  constructor(dialogRef: MockMatDialogRef<McpServerCreate | McpServerUpdate | null>, data?: DialogData) {
    this.dialogRef = dialogRef;
    this.data = data || null;
    this.isEditMode = computed(() => !!this.data?.server && !this.data?.server.is_builtin);
    this.isBuiltinConfigureMode = computed(() => !!this.data?.server?.is_builtin);
    this.isTemplateMode = computed(() => !!this.data?.template);

    if (this.data?.server && !this.data.server.is_builtin) {
      this.initializeFromServer(this.data.server);
    } else if (this.data?.server?.is_builtin) {
      // Builtin configure mode - initialize schema form with existing values
      if (this.data.server.initial_values) {
        this.schemaFormValues.set({ ...this.data.server.initial_values });
      }
    }
  }

  private initializeFromServer(server: McpServer): void {
    this.name.set(server.name);
    this.description.set(server.description || '');
    this.isActive.set(server.is_active);
    if (server.config && Object.keys(server.config).length > 0) {
      this.configJson.set(JSON.stringify(server.config, null, 2));
    }
  }

  onSchemaValuesChange(values: Record<string, unknown>): void {
    this.schemaFormValues.set(values);
  }

  onSchemaValidChange(isValid: boolean): void {
    this.schemaFormValid.set(isValid);
  }

  onNameChange(event: Event): void {
    const target = event.target as HTMLInputElement;
    this.name.set(target.value);
  }

  onDescriptionChange(event: Event): void {
    const target = event.target as HTMLTextAreaElement;
    this.description.set(target.value);
  }

  onConfigJsonChange(event: Event): void {
    const target = event.target as HTMLTextAreaElement;
    this.configJson.set(target.value);
    this.validateConfigJson();
  }

  onIsActiveChange(event: Event): void {
    const target = event.target as HTMLInputElement;
    this.isActive.set(target.checked);
  }

  selectTemplate(type: string): void {
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

  formatJson(): void {
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

  onConfigKeydown(event: KeyboardEvent): void {
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

  handleError(context: string, err: unknown): void {
    this.saving.set(false);
    console.error(`Failed to ${context}:`, err);
    const message = (err as any)?.error?.detail || (err as any)?.message || `Failed to ${context}`;
    this.snackBar.open(message, 'Close', { duration: 5000, panelClass: 'error-snackbar' });
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

  handleClose(): void {
    this.dialogRef.close(null);
  }

  handleSubmit(): void {
    this.error.set(null);

    const nameValue = this.name().trim();
    if (!nameValue) {
      this.error.set('Name is required');
      return;
    }
    if (nameValue.length > 128) {
      this.error.set('Name must be 128 characters or less');
      return;
    }

    if (!this.validateConfigJson()) {
      return;
    }

    let config: Record<string, unknown> | undefined;
    const configJson = this.configJson().trim();
    if (configJson) {
      config = JSON.parse(configJson);
    }

    if (this.isEditMode() && this.data?.server) {
      const update: McpServerUpdate = {
        name: nameValue,
        description: this.description().trim() || null,
        config,
        is_active: this.isActive(),
      };
      this.dialogRef.close(update);
    } else {
      const create: McpServerCreate = {
        name: nameValue,
        description: this.description().trim() || null,
        config,
        is_active: this.isActive(),
      };
      this.dialogRef.close(create);
    }
  }

  isSubmitDisabled(): boolean {
    if (this.isBuiltinConfigureMode() || this.isTemplateMode()) {
      return !this.schemaFormValid();
    }
    return !this.name().trim() || this.configJsonError() !== null;
  }
}

// Helper to create mock MCP server
function createMockServer(overrides: Partial<McpServer> = {}): McpServer {
  return {
    id: `server-${Math.random().toString(36).substr(2, 9)}`,
    name: 'Test MCP Server',
    description: 'A test MCP server',
    config: { command: 'npx', args: ['test-server'] },
    is_active: true,
    created_at: '2025-01-15T10:30:00Z',
    updated_at: null,
    ...overrides,
  };
}

// Helper to create mock builtin server
function createMockBuiltinServer(overrides: Partial<McpServer> = {}): McpServer {
  return {
    id: `builtin-${Math.random().toString(36).substr(2, 9)}`,
    name: 'Test Builtin Server',
    description: 'A test builtin MCP server',
    config: {},
    is_active: true,
    is_builtin: true,
    config_schema: [
      { key: 'api_key', label: 'API Key', type: 'text', section: 'env', required: true },
      { key: 'debug', label: 'Debug Mode', type: 'boolean', section: 'args', default: false }
    ],
    initial_values: { api_key: '', debug: false },
    created_at: '2025-01-15T10:30:00Z',
    updated_at: null,
    ...overrides,
  };
}

// Helper to create mock template
function createMockTemplate(overrides: Partial<BuiltinServerTemplate> = {}): BuiltinServerTemplate {
  return {
    name: 'test-template',
    description: 'A test builtin template',
    config_schema: [
      { key: 'api_key', label: 'API Key', type: 'text', section: 'env', required: true },
      { key: 'timeout', label: 'Timeout', type: 'number', section: 'args', default: 30, min: 1, max: 300 }
    ],
    ...overrides,
  };
}

describe('McpServerDialogComponent', () => {
  let dialogRef: MockMatDialogRef<McpServerCreate | McpServerUpdate | null>;

  beforeEach(() => {
    dialogRef = new MockMatDialogRef();
  });

  describe('create mode', () => {
    let component: TestableMcpServerDialogComponent;

    beforeEach(() => {
      component = new TestableMcpServerDialogComponent(dialogRef);
    });

    it('should create successfully in create mode', () => {
      expect(component).toBeDefined();
    });

    it('should not be in edit mode', () => {
      expect(component.isEditMode()).toBe(false);
    });

    it('should have empty name signal', () => {
      expect(component.name()).toBe('');
    });

    it('should have empty description signal', () => {
      expect(component.description()).toBe('');
    });

    it('should have empty configJson signal', () => {
      expect(component.configJson()).toBe('');
    });

    it('should have isActive set to true by default', () => {
      expect(component.isActive()).toBe(true);
    });

    it('should have null error signal', () => {
      expect(component.error()).toBeNull();
    });

    it('should have null configJsonError signal', () => {
      expect(component.configJsonError()).toBeNull();
    });
  });

  describe('edit mode', () => {
    let component: TestableMcpServerDialogComponent;
    let mockServer: McpServer;

    beforeEach(() => {
      mockServer = createMockServer({
        id: 'server-123',
        name: 'Existing Server',
        description: 'Existing description',
        is_active: false,
        config: { command: 'npx', args: ['server'] },
      });
      component = new TestableMcpServerDialogComponent(dialogRef, { server: mockServer });
    });

    it('should create successfully in edit mode', () => {
      expect(component).toBeDefined();
    });

    it('should be in edit mode', () => {
      expect(component.isEditMode()).toBe(true);
    });

    it('should pre-fill name from server data', () => {
      expect(component.name()).toBe('Existing Server');
    });

    it('should pre-fill description from server data', () => {
      expect(component.description()).toBe('Existing description');
    });

    it('should pre-fill isActive from server data', () => {
      expect(component.isActive()).toBe(false);
    });

    it('should pre-fill configJson from server config', () => {
      const config = component.configJson();
      expect(config).toContain('command');
      expect(config).toContain('npx');
    });

    it('should handle null description', () => {
      const serverWithNullDesc = createMockServer({ description: null });
      const comp = new TestableMcpServerDialogComponent(dialogRef, { server: serverWithNullDesc });
      expect(comp.description()).toBe('');
    });

    it('should handle empty config object', () => {
      const serverWithEmptyConfig = createMockServer({ config: {} });
      const comp = new TestableMcpServerDialogComponent(dialogRef, { server: serverWithEmptyConfig });
      expect(comp.configJson()).toBe('');
    });

    it('should handle config with nested objects', () => {
      const serverWithNestedConfig = createMockServer({
        config: {
          nested: { deep: { value: 123 } },
          array: [1, 2, 3],
        },
      });
      const comp = new TestableMcpServerDialogComponent(dialogRef, { server: serverWithNestedConfig });
      const config = comp.configJson();
      expect(config).toContain('nested');
      expect(config).toContain('deep');
    });
  });

  describe('form field changes', () => {
    let component: TestableMcpServerDialogComponent;

    beforeEach(() => {
      component = new TestableMcpServerDialogComponent(dialogRef);
    });

    it('should update name on onNameChange', () => {
      const event = { target: { value: 'New Name' } } as unknown as Event;
      component.onNameChange(event);
      expect(component.name()).toBe('New Name');
    });

    it('should update description on onDescriptionChange', () => {
      const event = { target: { value: 'New Description' } } as unknown as Event;
      component.onDescriptionChange(event);
      expect(component.description()).toBe('New Description');
    });

    it('should update configJson on onConfigJsonChange', () => {
      const event = { target: { value: '{"key": "value"}' } } as unknown as Event;
      component.onConfigJsonChange(event);
      expect(component.configJson()).toBe('{"key": "value"}');
    });

    it('should update isActive on onIsActiveChange when checked', () => {
      const event = { target: { checked: true } } as unknown as Event;
      component.onIsActiveChange(event);
      expect(component.isActive()).toBe(true);
    });

    it('should update isActive on onIsActiveChange when unchecked', () => {
      component.isActive.set(true);
      const event = { target: { checked: false } } as unknown as Event;
      component.onIsActiveChange(event);
      expect(component.isActive()).toBe(false);
    });

    it('should clear configJsonError on valid JSON change', () => {
      component.configJsonError.set('Previous error');
      const event = { target: { value: '{"valid": true}' } } as unknown as Event;
      component.onConfigJsonChange(event);
      expect(component.configJsonError()).toBeNull();
    });

    it('should set configJsonError on invalid JSON change', () => {
      const event = { target: { value: '{invalid json}' } } as unknown as Event;
      component.onConfigJsonChange(event);
      expect(component.configJsonError()).toBe('Invalid JSON format');
    });
  });

  describe('validation', () => {
    describe('name validation', () => {
      it('should set error when name is empty', () => {
        const component = new TestableMcpServerDialogComponent(dialogRef);
        component.name.set('');
        component.description.set('desc');
        component.handleSubmit();
        expect(component.error()).toBe('Name is required');
      });

      it('should set error when name is whitespace only', () => {
        const component = new TestableMcpServerDialogComponent(dialogRef);
        component.name.set('   ');
        component.handleSubmit();
        expect(component.error()).toBe('Name is required');
      });

      it('should set error when name exceeds 128 characters', () => {
        const component = new TestableMcpServerDialogComponent(dialogRef);
        component.name.set('a'.repeat(129));
        component.handleSubmit();
        expect(component.error()).toBe('Name must be 128 characters or less');
      });

      it('should accept name with exactly 128 characters', () => {
        const component = new TestableMcpServerDialogComponent(dialogRef);
        component.name.set('a'.repeat(128));
        const closeSpy = jest.spyOn(dialogRef, 'close');
        component.handleSubmit();
        expect(component.error()).toBeNull();
        expect(closeSpy).toHaveBeenCalled();
      });

      it('should clear error when valid name provided', () => {
        const component = new TestableMcpServerDialogComponent(dialogRef);
        component.error.set('Previous error');
        component.name.set('Valid Name');
        component.handleSubmit();
        expect(component.error()).toBeNull();
      });
    });

    describe('JSON config validation', () => {
      it('should accept empty config (optional field)', () => {
        const component = new TestableMcpServerDialogComponent(dialogRef);
        component.name.set('Valid Name');
        component.configJson.set('');
        const closeSpy = jest.spyOn(dialogRef, 'close');
        component.handleSubmit();
        expect(component.configJsonError()).toBeNull();
        expect(closeSpy).toHaveBeenCalled();
      });

      it('should accept valid JSON object', () => {
        const component = new TestableMcpServerDialogComponent(dialogRef);
        component.name.set('Valid Name');
        component.configJson.set('{"command": "npx"}');
        const closeSpy = jest.spyOn(dialogRef, 'close');
        component.handleSubmit();
        expect(closeSpy).toHaveBeenCalled();
      });

      it('should accept valid JSON with nested objects', () => {
        const component = new TestableMcpServerDialogComponent(dialogRef);
        component.name.set('Valid Name');
        const nestedConfig = JSON.stringify({
          nested: { deep: { value: 123 } },
        });
        component.configJson.set(nestedConfig);
        const closeSpy = jest.spyOn(dialogRef, 'close');
        component.handleSubmit();
        expect(closeSpy).toHaveBeenCalled();
      });

      it('should accept valid JSON array', () => {
        const component = new TestableMcpServerDialogComponent(dialogRef);
        component.name.set('Valid Name');
        component.configJson.set('["item1", "item2"]');
        const closeSpy = jest.spyOn(dialogRef, 'close');
        component.handleSubmit();
        expect(closeSpy).toHaveBeenCalled();
      });

      it('should accept valid JSON primitive', () => {
        const component = new TestableMcpServerDialogComponent(dialogRef);
        component.name.set('Valid Name');
        component.configJson.set('"string value"');
        const closeSpy = jest.spyOn(dialogRef, 'close');
        component.handleSubmit();
        expect(closeSpy).toHaveBeenCalled();
      });

      it('should reject invalid JSON', () => {
        const component = new TestableMcpServerDialogComponent(dialogRef);
        component.name.set('Valid Name');
        component.configJson.set('{invalid}');
        const closeSpy = jest.spyOn(dialogRef, 'close');
        component.handleSubmit();
        expect(component.configJsonError()).toBe('Invalid JSON format');
        expect(closeSpy).not.toHaveBeenCalled();
      });

      it('should reject JSON with syntax errors', () => {
        const component = new TestableMcpServerDialogComponent(dialogRef);
        component.name.set('Valid Name');
        component.configJson.set('{"key": }');
        const closeSpy = jest.spyOn(dialogRef, 'close');
        component.handleSubmit();
        expect(component.configJsonError()).toBe('Invalid JSON format');
        expect(closeSpy).not.toHaveBeenCalled();
      });

      it('should reject unquoted JSON keys', () => {
        const component = new TestableMcpServerDialogComponent(dialogRef);
        component.name.set('Valid Name');
        component.configJson.set('{key: "value"}');
        const closeSpy = jest.spyOn(dialogRef, 'close');
        component.handleSubmit();
        expect(component.configJsonError()).toBe('Invalid JSON format');
        expect(closeSpy).not.toHaveBeenCalled();
      });

      it('should trim whitespace before JSON validation', () => {
        const component = new TestableMcpServerDialogComponent(dialogRef);
        component.name.set('Valid Name');
        component.configJson.set('   {"key": "value"}   ');
        const closeSpy = jest.spyOn(dialogRef, 'close');
        component.handleSubmit();
        expect(component.configJsonError()).toBeNull();
        expect(closeSpy).toHaveBeenCalled();
      });

      it('should set error on invalid JSON but not clear previous validation error', () => {
        const component = new TestableMcpServerDialogComponent(dialogRef);
        component.name.set('Valid Name');
        component.configJson.set('{invalid}');
        component.handleSubmit();
        expect(component.configJsonError()).toBe('Invalid JSON format');
      });
    });
  });

  describe('handleSubmit in create mode', () => {
    let component: TestableMcpServerDialogComponent;
    let closeSpy: jest.SpyInstance;

    beforeEach(() => {
      component = new TestableMcpServerDialogComponent(dialogRef);
      closeSpy = jest.spyOn(dialogRef, 'close');
    });

    it('should emit McpServerCreate data on successful submit', () => {
      component.name.set('New Server');
      component.description.set('A new server');
      component.configJson.set('{"command": "npx"}');
      component.isActive.set(true);

      component.handleSubmit();

      expect(closeSpy).toHaveBeenCalledWith({
        name: 'New Server',
        description: 'A new server',
        config: { command: 'npx' },
        is_active: true,
      });
    });

    it('should emit with null description when empty', () => {
      component.name.set('New Server');
      component.description.set('');
      component.isActive.set(true);

      component.handleSubmit();

      expect(closeSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'New Server',
          description: null,
        })
      );
    });

    it('should emit with whitespace description trimmed to null', () => {
      component.name.set('New Server');
      component.description.set('   ');
      component.isActive.set(true);

      component.handleSubmit();

      expect(closeSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          description: null,
        })
      );
    });

    it('should emit with undefined config when empty', () => {
      component.name.set('New Server');
      component.description.set('desc');
      component.configJson.set('');
      component.isActive.set(true);

      component.handleSubmit();

      expect(closeSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          config: undefined,
        })
      );
    });

    it('should emit is_active as true by default', () => {
      component.name.set('New Server');
      component.description.set('desc');
      component.isActive.set(true);

      component.handleSubmit();

      expect(closeSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          is_active: true,
        })
      );
    });

    it('should emit is_active as false when unchecked', () => {
      component.name.set('New Server');
      component.description.set('desc');
      component.isActive.set(false);

      component.handleSubmit();

      expect(closeSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          is_active: false,
        })
      );
    });

    it('should parse JSON config correctly', () => {
      component.name.set('New Server');
      component.description.set('desc');
      component.configJson.set(JSON.stringify({
        command: 'npx',
        args: ['-y', '@server/package'],
        env: { KEY: 'value' },
      }));
      component.isActive.set(true);

      component.handleSubmit();

      expect(closeSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          config: {
            command: 'npx',
            args: ['-y', '@server/package'],
            env: { KEY: 'value' },
          },
        })
      );
    });
  });

  describe('handleSubmit in edit mode', () => {
    let component: TestableMcpServerDialogComponent;
    let closeSpy: jest.SpyInstance;
    let mockServer: McpServer;

    beforeEach(() => {
      mockServer = createMockServer({
        id: 'server-123',
        name: 'Original Name',
        description: 'Original description',
        is_active: true,
        config: { command: 'original' },
      });
      component = new TestableMcpServerDialogComponent(dialogRef, { server: mockServer });
      closeSpy = jest.spyOn(dialogRef, 'close');
    });

    it('should emit McpServerUpdate data on successful submit', () => {
      component.name.set('Updated Name');
      component.description.set('Updated description');
      component.isActive.set(false);

      component.handleSubmit();

      expect(closeSpy).toHaveBeenCalledWith({
        name: 'Updated Name',
        description: 'Updated description',
        config: { command: 'original' },
        is_active: false,
      });
    });

    it('should preserve original config when configJson is empty', () => {
      component.configJson.set('');
      component.name.set('Updated Name');
      component.description.set('desc');
      component.isActive.set(true);

      component.handleSubmit();

      expect(closeSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          config: undefined,
        })
      );
    });

    it('should override config when new JSON provided', () => {
      component.name.set('Updated Name');
      component.configJson.set('{"new": "config"}');
      component.description.set('');
      component.isActive.set(true);

      component.handleSubmit();

      expect(closeSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          config: { new: 'config' },
        })
      );
    });
  });

  describe('handleClose', () => {
    it('should emit null on handleClose', () => {
      const component = new TestableMcpServerDialogComponent(dialogRef);
      const closeSpy = jest.spyOn(dialogRef, 'close');

      component.handleClose();

      expect(closeSpy).toHaveBeenCalledWith(null);
    });

    it('should not validate form on handleClose', () => {
      const component = new TestableMcpServerDialogComponent(dialogRef);
      const closeSpy = jest.spyOn(dialogRef, 'close');

      // Name is empty but should still close
      component.handleClose();

      expect(closeSpy).toHaveBeenCalledWith(null);
    });
  });

  describe('isSubmitDisabled', () => {
    it('should return true when name is empty', () => {
      const component = new TestableMcpServerDialogComponent(dialogRef);
      component.name.set('');
      expect(component.isSubmitDisabled()).toBe(true);
    });

    it('should return true when name is whitespace only', () => {
      const component = new TestableMcpServerDialogComponent(dialogRef);
      component.name.set('   ');
      expect(component.isSubmitDisabled()).toBe(true);
    });

    it('should return true when configJsonError is set', () => {
      const component = new TestableMcpServerDialogComponent(dialogRef);
      component.name.set('Valid Name');
      component.configJsonError.set('Invalid JSON');
      expect(component.isSubmitDisabled()).toBe(true);
    });

    it('should return false when name is valid and no config error', () => {
      const component = new TestableMcpServerDialogComponent(dialogRef);
      component.name.set('Valid Name');
      component.configJsonError.set(null);
      expect(component.isSubmitDisabled()).toBe(false);
    });

    it('should return false when name is valid and configJson is empty', () => {
      const component = new TestableMcpServerDialogComponent(dialogRef);
      component.name.set('Valid Name');
      component.configJson.set('');
      component.configJsonError.set(null);
      expect(component.isSubmitDisabled()).toBe(false);
    });

    it('should return false when name is valid and configJson has valid JSON', () => {
      const component = new TestableMcpServerDialogComponent(dialogRef);
      component.name.set('Valid Name');
      component.configJson.set('{"key": "value"}');
      component.configJsonError.set(null);
      expect(component.isSubmitDisabled()).toBe(false);
    });

    it('should return true when name is valid but JSON is invalid', () => {
      const component = new TestableMcpServerDialogComponent(dialogRef);
      component.name.set('Valid Name');
      component.configJson.set('{invalid}');
      // Trigger validation
      component.onConfigJsonChange({ target: { value: '{invalid}' } } as unknown as Event);
      expect(component.isSubmitDisabled()).toBe(true);
    });

    it('should update reactively when name changes', () => {
      const component = new TestableMcpServerDialogComponent(dialogRef);
      expect(component.isSubmitDisabled()).toBe(true);

      component.name.set('Valid Name');
      expect(component.isSubmitDisabled()).toBe(false);

      component.name.set('');
      expect(component.isSubmitDisabled()).toBe(true);
    });
  });

  describe('form state management', () => {
    it('should reset error on submit attempt', () => {
      const component = new TestableMcpServerDialogComponent(dialogRef);
      component.error.set('Previous error');
      component.name.set('Valid Name');

      component.handleSubmit();

      expect(component.error()).toBeNull();
    });

    it('should not reset error on handleClose', () => {
      const component = new TestableMcpServerDialogComponent(dialogRef);
      component.error.set('Some error');

      component.handleClose();

      expect(component.error()).toBe('Some error');
    });

    it('should handle rapid field changes', () => {
      const component = new TestableMcpServerDialogComponent(dialogRef);

      component.name.set('Name 1');
      component.description.set('Desc 1');
      component.configJson.set('{"a": 1}');

      component.name.set('Name 2');
      component.description.set('Desc 2');
      component.configJson.set('{"b": 2}');

      expect(component.name()).toBe('Name 2');
      expect(component.description()).toBe('Desc 2');
      expect(component.configJson()).toBe('{"b": 2}');
    });

    it('should allow editing after error is cleared', () => {
      const component = new TestableMcpServerDialogComponent(dialogRef);
      const closeSpy = jest.spyOn(dialogRef, 'close');

      // First submit fails
      component.handleSubmit();
      expect(component.error()).toBe('Name is required');
      expect(closeSpy).not.toHaveBeenCalled();

      // Fix the error
      component.name.set('Valid Name');
      component.handleSubmit();

      expect(component.error()).toBeNull();
      expect(closeSpy).toHaveBeenCalled();
    });
  });

  describe('complex scenarios', () => {
    it('should handle server with all fields populated', () => {
      const fullServer: McpServer = {
        id: 'server-full',
        name: 'Full Server',
        description: 'A server with all fields',
        config: {
          command: 'npx',
          args: ['-y', '@server/package', '/path'],
          env: { DEBUG: 'true', PORT: '8080' },
          timeout: 30000,
        },
        is_active: true,
        created_at: '2025-01-15T10:30:00Z',
        updated_at: '2025-01-16T12:00:00Z',
      };

      const component = new TestableMcpServerDialogComponent(dialogRef, { server: fullServer });

      expect(component.name()).toBe('Full Server');
      expect(component.description()).toBe('A server with all fields');
      expect(component.isActive()).toBe(true);
      expect(component.configJson()).toContain('command');
      expect(component.configJson()).toContain('env');
    });

    it('should handle creating server with complex config', () => {
      const component = new TestableMcpServerDialogComponent(dialogRef);
      const closeSpy = jest.spyOn(dialogRef, 'close');

      component.name.set('Complex Server');
      component.description.set('Server with complex configuration');
      component.configJson.set(JSON.stringify({
        command: 'docker',
        args: ['run', '--rm', '-it', 'image:latest'],
        env: {
          API_KEY: 'secret',
          DB_HOST: 'localhost',
          DB_PORT: '5432',
        },
        ports: [{ host: 8080, container: 3000 }],
        volumes: ['/data:/app/data'],
      }));
      component.isActive.set(true);

      component.handleSubmit();

      const emittedData = closeSpy.mock.calls[0][0] as McpServerCreate;
      expect(emittedData.name).toBe('Complex Server');
      expect(emittedData.config).toEqual({
        command: 'docker',
        args: ['run', '--rm', '-it', 'image:latest'],
        env: {
          API_KEY: 'secret',
          DB_HOST: 'localhost',
          DB_PORT: '5432',
        },
        ports: [{ host: 8080, container: 3000 }],
        volumes: ['/data:/app/data'],
      });
    });

    it('should handle edit then cancel workflow', () => {
      const mockServer = createMockServer({ name: 'Original' });
      const component = new TestableMcpServerDialogComponent(dialogRef, { server: mockServer });

      // Make some changes
      component.name.set('Modified');
      component.description.set('New description');

      // Cancel
      const closeSpy = jest.spyOn(dialogRef, 'close');
      component.handleClose();

      expect(closeSpy).toHaveBeenCalledWith(null);
    });
  });

  describe('mode detection', () => {
    it('should detect builtin configure mode', () => {
      const builtinServer = createMockBuiltinServer();
      const component = new TestableMcpServerDialogComponent(dialogRef, { server: builtinServer });

      expect(component.isBuiltinConfigureMode()).toBe(true);
      expect(component.isEditMode()).toBe(false);
      expect(component.isTemplateMode()).toBe(false);
    });

    it('should detect template mode', () => {
      const template = createMockTemplate();
      const component = new TestableMcpServerDialogComponent(dialogRef, { template });

      expect(component.isTemplateMode()).toBe(true);
      expect(component.isEditMode()).toBe(false);
      expect(component.isBuiltinConfigureMode()).toBe(false);
    });

    it('should not confuse builtin server as edit mode', () => {
      const builtinServer = createMockBuiltinServer();
      const component = new TestableMcpServerDialogComponent(dialogRef, { server: builtinServer });

      expect(component.isEditMode()).toBe(false);
    });

    it('should detect both modes when server and template are present', () => {
      // In practice, dialog data would have either server or template, not both
      // But computed signals check independently
      const builtinServer = createMockBuiltinServer();
      const component = new TestableMcpServerDialogComponent(dialogRef, {
        server: builtinServer,
        template: createMockTemplate()
      });

      // Both would be true in this edge case
      expect(component.isBuiltinConfigureMode()).toBe(true);
      expect(component.isTemplateMode()).toBe(true);
    });
  });

  describe('schema form state', () => {
    it('should initialize with empty schema values in create mode', () => {
      const component = new TestableMcpServerDialogComponent(dialogRef);
      expect(component.schemaFormValues()).toEqual({});
    });

    it('should initialize with initial_values in builtin configure mode', () => {
      const builtinServer = createMockBuiltinServer({
        initial_values: { api_key: 'secret123', debug: true }
      });
      const component = new TestableMcpServerDialogComponent(dialogRef, { server: builtinServer });

      expect(component.schemaFormValues()).toEqual({ api_key: 'secret123', debug: true });
    });

    it('should update schema form values on onSchemaValuesChange', () => {
      const component = new TestableMcpServerDialogComponent(dialogRef);
      component.onSchemaValuesChange({ api_key: 'newkey', timeout: 60 });

      expect(component.schemaFormValues()).toEqual({ api_key: 'newkey', timeout: 60 });
    });

    it('should update schema form validity on onSchemaValidChange', () => {
      const component = new TestableMcpServerDialogComponent(dialogRef);

      component.onSchemaValidChange(true);
      expect(component.schemaFormValid()).toBe(true);

      component.onSchemaValidChange(false);
      expect(component.schemaFormValid()).toBe(false);
    });
  });

  describe('isSubmitDisabled in schema modes', () => {
    it('should disable submit in builtin mode when schema form is invalid', () => {
      const builtinServer = createMockBuiltinServer();
      const component = new TestableMcpServerDialogComponent(dialogRef, { server: builtinServer });

      component.onSchemaValidChange(false);
      expect(component.isSubmitDisabled()).toBe(true);
    });

    it('should enable submit in builtin mode when schema form is valid', () => {
      const builtinServer = createMockBuiltinServer();
      const component = new TestableMcpServerDialogComponent(dialogRef, { server: builtinServer });

      component.onSchemaValidChange(true);
      expect(component.isSubmitDisabled()).toBe(false);
    });

    it('should disable submit in template mode when schema form is invalid', () => {
      const template = createMockTemplate();
      const component = new TestableMcpServerDialogComponent(dialogRef, { template });

      component.onSchemaValidChange(false);
      expect(component.isSubmitDisabled()).toBe(true);
    });

    it('should enable submit in template mode when schema form is valid', () => {
      const template = createMockTemplate();
      const component = new TestableMcpServerDialogComponent(dialogRef, { template });

      component.onSchemaValidChange(true);
      expect(component.isSubmitDisabled()).toBe(false);
    });
  });

  describe('template selection', () => {
    let component: TestableMcpServerDialogComponent;

    beforeEach(() => {
      component = new TestableMcpServerDialogComponent(dialogRef);
      MockMatSnackBar.reset();
    });

    it('should select stdio template and fill correct JSON', () => {
      component.selectTemplate('stdio');

      expect(component.selectedTemplate()).toBe('stdio');
      const config = JSON.parse(component.configJson());
      expect(config).toEqual({
        transport: 'stdio',
        command: 'npx',
        args: ['-y', '@example/mcp-server']
      });
    });

    it('should select sse template and fill correct JSON', () => {
      component.selectTemplate('sse');

      expect(component.selectedTemplate()).toBe('sse');
      const config = JSON.parse(component.configJson());
      expect(config).toEqual({
        transport: 'sse',
        url: 'http://localhost:3000/sse',
        headers: {
          Authorization: 'Bearer YOUR_TOKEN_HERE'
        }
      });
    });

    it('should select streamable-http template and fill correct JSON', () => {
      component.selectTemplate('streamable-http');

      expect(component.selectedTemplate()).toBe('streamable-http');
      const config = JSON.parse(component.configJson());
      expect(config).toEqual({
        transport: 'streamable-http',
        url: 'http://localhost:3000/mcp',
        headers: {
          Authorization: 'Bearer YOUR_TOKEN_HERE'
        }
      });
    });

    it('should clear previous config when selecting new template', () => {
      component.configJson.set('{"old": "config"}');
      component.selectTemplate('stdio');

      const config = JSON.parse(component.configJson());
      expect(config).toEqual({
        transport: 'stdio',
        command: 'npx',
        args: ['-y', '@example/mcp-server']
      });
      expect(config.old).toBeUndefined();
    });

    it('should deselect template when clicking the same template again', () => {
      component.selectTemplate('stdio');
      expect(component.selectedTemplate()).toBe('stdio');

      component.selectTemplate('stdio');
      expect(component.selectedTemplate()).toBeNull();
    });

    it('should keep content when deselecting template', () => {
      component.selectTemplate('stdio');
      const originalJson = component.configJson();

      component.selectTemplate('stdio');

      expect(component.configJson()).toBe(originalJson);
    });

    it('should switch templates when clicking different template', () => {
      component.selectTemplate('stdio');
      expect(component.selectedTemplate()).toBe('stdio');

      component.selectTemplate('sse');
      expect(component.selectedTemplate()).toBe('sse');
      const config = JSON.parse(component.configJson());
      expect(config.transport).toBe('sse');
    });
  });

  describe('formatJson', () => {
    let component: TestableMcpServerDialogComponent;

    beforeEach(() => {
      component = new TestableMcpServerDialogComponent(dialogRef);
    });

    it('should pretty-print single-line JSON', () => {
      component.configJson.set('{"key":"value","number":42}');

      component.formatJson();

      const expected = JSON.stringify({ key: 'value', number: 42 }, null, 2);
      expect(component.configJson()).toBe(expected);
    });

    it('should preserve already formatted JSON', () => {
      const formatted = JSON.stringify({ key: 'value' }, null, 2);
      component.configJson.set(formatted);

      component.formatJson();

      expect(component.configJson()).toBe(formatted);
    });

    it('should handle nested objects', () => {
      component.configJson.set('{"outer":{"inner":"value"}}');

      component.formatJson();

      const config = JSON.parse(component.configJson());
      expect(config).toEqual({ outer: { inner: 'value' } });
      // Verify it's multi-line (contains newlines)
      expect(component.configJson()).toContain('\n');
    });

    it('should handle arrays', () => {
      component.configJson.set('[1,2,3]');

      component.formatJson();

      expect(component.configJson()).toBe(JSON.stringify([1, 2, 3], null, 2));
    });

    it('should do nothing for empty config', () => {
      component.configJson.set('');

      component.formatJson();

      expect(component.configJson()).toBe('');
    });

    it('should do nothing for whitespace-only config', () => {
      component.configJson.set('   ');

      component.formatJson();

      expect(component.configJson()).toBe('   ');
    });

    it('should not format invalid JSON', () => {
      component.configJson.set('{invalid}');

      component.formatJson();

      expect(component.configJson()).toBe('{invalid}');
    });

    it('should clear previous validation error when formatting valid JSON', () => {
      component.configJsonError.set('Previous error');
      component.configJson.set('{"valid":true}');

      component.formatJson();

      expect(component.configJsonError()).toBeNull();
    });
  });

  describe('onConfigKeydown', () => {
    let component: TestableMcpServerDialogComponent;

    beforeEach(() => {
      component = new TestableMcpServerDialogComponent(dialogRef);
    });

    it('should insert 2 spaces on Tab key', () => {
      component.configJson.set('line1\ncursor');
      // Position 6 is after the newline, so spaces go at start of second line
      const event = {
        key: 'Tab',
        target: { selectionStart: 6, selectionEnd: 6 },
        preventDefault: jest.fn()
      } as unknown as KeyboardEvent;

      component.onConfigKeydown(event);

      expect(event.preventDefault).toHaveBeenCalled();
      expect(component.configJson()).toBe('line1\n  cursor');
    });

    it('should replace selected text with 2 spaces', () => {
      component.configJson.set('beforeSELECTafter');
      const event = {
        key: 'Tab',
        target: { selectionStart: 6, selectionEnd: 12 },
        preventDefault: jest.fn()
      } as unknown as KeyboardEvent;

      component.onConfigKeydown(event);

      expect(component.configJson()).toBe('before  after');
    });

    it('should not handle non-Tab keys', () => {
      component.configJson.set('original');
      const event = {
        key: 'Enter',
        target: { selectionStart: 0, selectionEnd: 0 },
        preventDefault: jest.fn()
      } as unknown as KeyboardEvent;

      component.onConfigKeydown(event);

      expect(event.preventDefault).not.toHaveBeenCalled();
      expect(component.configJson()).toBe('original');
    });

    it('should handle Tab at start of text', () => {
      component.configJson.set('text');
      const event = {
        key: 'Tab',
        target: { selectionStart: 0, selectionEnd: 0 },
        preventDefault: jest.fn()
      } as unknown as KeyboardEvent;

      component.onConfigKeydown(event);

      expect(component.configJson()).toBe('  text');
    });

    it('should handle Tab at end of text', () => {
      component.configJson.set('text');
      const event = {
        key: 'Tab',
        target: { selectionStart: 4, selectionEnd: 4 },
        preventDefault: jest.fn()
      } as unknown as KeyboardEvent;

      component.onConfigKeydown(event);

      expect(component.configJson()).toBe('text  ');
    });
  });

  describe('handleError', () => {
    let component: TestableMcpServerDialogComponent;
    let consoleErrorSpy: jest.SpyInstance;

    beforeEach(() => {
      component = new TestableMcpServerDialogComponent(dialogRef);
      MockMatSnackBar.reset();
      consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation();
    });

    afterEach(() => {
      consoleErrorSpy.mockRestore();
    });

    it('should set saving to false', () => {
      component.saving.set(true);

      component.handleError('test operation', new Error('test error'));

      expect(component.saving()).toBe(false);
    });

    it('should log error to console', () => {
      const error = new Error('test error');

      component.handleError('test operation', error);

      expect(consoleErrorSpy).toHaveBeenCalledWith('Failed to test operation:', error);
    });

    it('should open snackbar with error message from Error object', () => {
      const error = new Error('Something went wrong');

      component.handleError('save data', error);

      expect(MockMatSnackBar.lastOpen).toEqual({
        message: 'Something went wrong',
        action: 'Close',
        options: { duration: 5000, panelClass: 'error-snackbar' }
      });
    });

    it('should open snackbar with detail from HTTP error', () => {
      const error = { error: { detail: 'Server unavailable' } };

      component.handleError('connect', error);

      expect(MockMatSnackBar.lastOpen?.message).toBe('Server unavailable');
    });

    it('should open snackbar with generic message when no specific error', () => {
      const error = { code: 'UNKNOWN' };

      component.handleError('process', error);

      expect(MockMatSnackBar.lastOpen?.message).toBe('Failed to process');
    });

    it('should handle null/undefined error gracefully', () => {
      expect(() => component.handleError('do something', null)).not.toThrow();
      expect(() => component.handleError('do something', undefined)).not.toThrow();
    });

    it('should handle error with message property', () => {
      const error = { message: 'Custom error message' };

      component.handleError('custom operation', error);

      expect(MockMatSnackBar.lastOpen?.message).toBe('Custom error message');
    });
  });

  describe('saving signal', () => {
    let component: TestableMcpServerDialogComponent;

    beforeEach(() => {
      component = new TestableMcpServerDialogComponent(dialogRef);
    });

    it('should initialize with saving as false', () => {
      expect(component.saving()).toBe(false);
    });

    it('should be settable', () => {
      component.saving.set(true);
      expect(component.saving()).toBe(true);

      component.saving.set(false);
      expect(component.saving()).toBe(false);
    });
  });

  describe('selectedTemplate signal', () => {
    let component: TestableMcpServerDialogComponent;

    beforeEach(() => {
      component = new TestableMcpServerDialogComponent(dialogRef);
    });

    it('should initialize with null', () => {
      expect(component.selectedTemplate()).toBeNull();
    });
  });
});
