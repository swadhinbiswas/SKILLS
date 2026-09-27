---
name: java-spring-patterns
description: Spring Boot patterns and the traps a general model gets wrong - constructor injection over field @Autowired, @ConfigurationProperties with validation, the @Transactional self-invocation proxy failure, @ControllerAdvice error handling, and test slices (@WebMvcTest, @DataJpaTest, @SpringBootTest). Use when building or reviewing a Spring Boot service, when a transaction "is not rolling back", when a bean is unexpectedly a proxy, or when a user asks about DI, "why isn't my transaction working", or "how do I test just the controller". Triggers on "@Transactional", "@Autowired", "@ConfigurationProperties", "ApplicationContext", "proxy", "self-invocation", "ControllerAdvice", "WebMvcTest".
compatibility: Spring Boot 3.x (Spring Framework 6, Jakarta namespace jakarta.*). @MockBean is deprecated in favour of @MockitoBean in Spring Framework 6.2+.
metadata:
  version: "1.0"
---

# Java Spring Patterns

Spring's magic is a **proxy** in front of your object. Almost every "Spring
doesn't work" bug is a consequence of that: the proxy only intercepts calls
that arrive *through* it, so a self-invocation, a `private` method, or a
`new` of your own class bypasses the behaviour you annotated. Know where the
proxy boundaries are and most Spring confusion resolves.

## Workflow

- [ ] 1. Constructor-inject every dependency; no field `@Autowired`
- [ ] 2. `@ConfigurationProperties` + `@Validated` for every config class, so
      bad config fails at startup
- [ ] 3. `@Transactional` on the **public** method that starts the
      transaction, and understand the self-invocation trap
- [ ] 4. One `@RestControllerAdvice` that turns exceptions into one response
      shape
- [ ] 5. Pick the narrowest test slice that can fail the build
- [ ] 6. `mvn -q verify` / `./gradlew build` with the test slice in CI

## DI: constructor injection, always

```java
@Service
public class OrderService {
    private final OrderRepository repo;      // final
    private final PaymentGateway payments;

    public OrderService(OrderRepository repo, PaymentGateway payments) {  // one ctor, no @Autowired needed
        this.repo = repo;
        this.payments = payments;
    }
}
```

- **A single constructor is autowired implicitly** — do not add `@Autowired` to
  it. This is the strongest reason to have exactly one.
- Field injection (`@Autowired` on a field) works but hides the dependency,
  makes the class untestable without a container, prevents `final`, and lets
  you instantiate a half-built object. Reserve it for framework-required
  field injection (rare). Use constructor injection everywhere else.
- Inject **interfaces at the boundary** when you want a test double; a
  concrete class can still be injected, which is fine.
- **`@Component` / `@Service` / `@Repository` / `@Controller` are the same
  mechanism with different stereotypes and default exception translation.**
  `@Repository` is the one that matters: it enables
  `PersistenceExceptionTranslationPostProcessor`, converting JPA's
  checked-ish exceptions into Spring's `DataAccessException` hierarchy. Use it
  for anything that touches a repository.
- Bean scope defaults to **singleton** — one instance for the whole
  application, shared across all threads. A singleton holding mutable
  request state is a data race. Keep beans stateless; pass per-request data as
  method arguments. `@RequestScope` creates a proxy that resolves per request
  (note: the bean is proxied, so a self-invocation on a request-scoped bean
  hits a different instance).
- Circular dependencies fail at startup by default
  (`BeanCurrentlyInCreationException`) since Boot 2.6 — by design, it usually
  indicates a design problem. Extract a shared collaborator; do not reach for
  `@Lazy` to hide it. (`spring.main.allow-circular-references=true` re-enables
  it, at the cost of proxies that may not be what you expect.)

## @ConfigurationProperties with validation

Bind config to a typed class and **validate it at startup**, so a bad
environment variable fails the deploy immediately, not the first request that
reads it.

```java
@ConfigurationProperties(prefix = "app")
@Validated                                       // required for @NotNull etc to fire
public record AppProperties(
    @NotBlank String databaseUrl,
    @Min(1) @Max(64) int workers,
    @NotNull @Valid PaymentProperties payment,   // @Valid cascades into nested
    Duration timeout                              // "30s" binds automatically
) {}

// Enable it: @EnableConfigurationProperties(AppProperties.class) on a @Configuration,
// or @ConfigurationPropertiesScan on the app class, or spring-boot-configuration-processor
// for IDE completion.
```

- In Boot 3, constructor/record binding means a single `@ConstructorBinding`
  constructor; setters are not needed. Records work well.
- Annotate with `@Validated` (jakarta.validation) or the `jakarta.validation`
  constraints on fields/record components are **ignored** — no
  `BindValidationException` at startup, and you get a null at runtime instead.
- Add `spring-boot-configuration-processor` (annotation processor) so the
  IDE autocompletes and you get a compile-time hint when a property is
  removed. `@ConfigurationProperties` classes are validated at startup; a bad
  value gives a clear `ConfigurationPropertiesBindException` naming the
  property — far better than a `null` three hours into a deploy.
- Property name mapping: `app.payment.stripe-key` →
  `PaymentProperties.stripeKey` (relaxed binding: kebab, snake, camel all
  work). Environment variables map via `APP_PAYMENT_STRIPE_KEY`.
- `@Value("${app.x}")` for one-off values is fine; for a group of related
  settings, use `@ConfigurationProperties` so they are typed and validated
  together.

## Transactions: the self-invocation trap

`@Transactional` is implemented with a **proxy** (JDK or CGLIB) around your
bean. The advice runs only when the call enters through the proxy. Therefore:

```java
@Service
public class OrderService {

    @Transactional                       // starts a transaction
    public Order place(OrderRequest req) {
        repo.save(order);
        this.applyDiscount(order);        // internal call — BYPASSES the proxy
        return order;
    }

    @Transactional                       // this annotation is IGNORED for the internal call
    protected void applyDiscount(Order o) { ... }
}
```

`this.applyDiscount(...)` is a plain Java call on `this`; there is no proxy,
so **no new transaction starts** and no advice runs. This is the single most
common Spring transaction bug, and it fails silently — the code "works" and
just is not transactional.

Fixes, in order of preference:

1. **Move the annotated method to a different bean** and call it through the
   injected reference. Now the call goes through that bean's proxy. This is
   the clean fix.
2. `AopContext.currentProxy()` — requires `exposeProxy = true`; it is a
   workaround, and it is easy to forget. Avoid unless you must.
3. Make the call go through `self = (OrderService) AopContext.currentProxy();`.
4. Restructure so the transactional boundary is the **public entry point** and
   internal methods just do work. Usually the best answer.

Other transaction traps:

- **`@Transactional` on a `private` method does nothing** — Spring's proxy
  (even CGLIB) cannot intercept a private call. Put it on `public` (or
  `protected`, CGLIB only).
- **`@Transactional` on a method that returns `void`/a non-future** rolls back
  on a thrown **RuntimeException** by default. **Checked exceptions do not
  roll back** unless you add `@Transactional(rollbackFor = Exception.class)`
  or `rollbackFor = IOException.class`. This surprises people writing
  `@Transactional` methods that catch and log.
- **`final` classes and `final`/`private` methods** cannot be proxied by
  CGLIB; Spring falls back to a JDK proxy which then only exposes interfaces.
  Boot's `proxy-target-class=true` (the default) uses CGLIB, so a `final`
  class silently loses the advice. Do not mark a `@Service` `final`.
- **Self-invocation also defeats `@Async`, `@Cacheable`, `@Retryable`,`@PreDestroy`
  and every other proxy-based advice** — same root cause.
- **Read-only transactions** (`@Transactional(readOnly = true)`) let the JPA
  provider skip dirty checking and route to a replica if configured. Use them
  for queries; do not mutate inside one.
- **`@Transactional` on a private or on a repository method called from a
  non-transactional service** is fine (the repository is proxied), but the
  transaction then covers only the repository call unless the service is also
  `@Transactional`. Understand your actual boundary.

## Exception handling and error responses

One `@RestControllerAdvice` maps exceptions to one response shape, so every
error looks the same. Pair it with the RFC 9457 problem-details contract from
`rest-api-implementation`.

```java
@RestControllerAdvice
public class ApiExceptionHandler {

    @ExceptionHandler(OrderNotFoundException.class)
    ResponseEntity<ProblemDetail> notFound(OrderNotFoundException e) {
        ProblemDetail pd = ProblemDetail.forStatusAndDetail(HttpStatus.NOT_FOUND, e.getMessage());
        pd.setTitle("Order not found");
        pd.setProperty("code", "order_not_found");
        return ResponseEntity.status(HttpStatus.NOT_FOUND).body(pd);
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    ResponseEntity<ProblemDetail> invalid(MethodArgumentNotValidException e) {
        ProblemDetail pd = ProblemDetail.forStatus(HttpStatus.UNPROCESSABLE_ENTITY);
        pd.setTitle("Validation failed");
        List<FieldError> errors = e.getBindingResult().getFieldErrors().stream()
            .map(f -> new FieldError(f.getField(), f.getCode(), f.getDefaultMessage()))
            .toList();
        pd.setProperty("errors", errors);
        return ResponseEntity.unprocessableEntity().body(pd);
    }
}
```

- Spring 6 / Boot 3 has `ProblemDetail` (RFC 9457) built in. Use it rather
  than a hand-rolled error class.
- **`@ExceptionHandler` does not catch async exceptions from a different
  thread.** An exception in an `@Async` method, a `CompletableFuture`, or a
  scheduler does not route through `@ControllerAdvice`. Wrap the async work
  and return the error through the future, or catch inside the task.
- **A missing handler means a `500` with Spring's default error body** (or,
  if `server.error.include-stacktrace=always`, a stack trace in the response —
  never enable that in production). Every custom exception class you throw
  from a controller needs a handler, or it is a 500.
- Do not catch `Exception` in a controller and return 200 with an error body.
  Let it propagate to the advice.
- Validation errors: `@Valid` on the controller method argument triggers
  `MethodArgumentNotValidException` (a `400` by default). Set the status in
  the handler (commonly `422` for well-formed-but-invalid, per the API
  contract).

## Testing: the right slice

Boot test slices start only the beans you need, so a unit test runs in
milliseconds instead of booting the whole app.

| Annotation | Boots | Use for |
|---|---|---|
| `@WebMvcTest(OrderController.class)` | Web layer + your controller, with **mocked** service beans | Controller routing, status codes, validation, serialisation, `@ControllerAdvice` |
| `@DataJpaTest` | JPA + an embedded/replaced DataSource (usually H2/Testcontainers) | Repository queries, custom JPQL/SQL, mappings, constraints |
| `@JsonTest` | Jackson only | (De)serialisation of DTOs |
| `@WebFluxTest` | Reactive web layer | WebFlux controllers |
| `@SpringBootTest` | **Everything** | A handful of true integration tests |

```java
@WebMvcTest(OrderController.class)
class OrderControllerTest {
    @Autowired MockMvc mvc;                        // MockMvc, no real port
    @MockitoBean OrderService orders;              // Boot 3.4/SF 6.2+; @MockBean before

    @Test void place_returns201() throws Exception {
        given(orders.place(any())).willReturn(new Order("o_1"));
        mvc.perform(post("/orders").contentType(APPLICATION_JSON)
                    .content("{\"sku\":\"A1\"}"))
           .andExpect(status().isCreated())
           .andExpect(jsonPath("$.id").value("o_1"));
    }
}
```

- **In Spring Framework 6.2 / Boot 3.4, `@MockBean` is deprecated in favour of
  `@MockitoBean`** (org.springframework.test.context.bean.override.mockito).
  Use `@MockitoBean` on 3.4+; on older versions use `@MockBean` and accept the
  deprecation. Check your Boot version — do not mix them.
- A `@WebMvcTest` does **not** load `@Service` beans or a real database, so a
  controller that autowires a repository directly fails to start. That is the
  slice telling you the layering is wrong.
- `@DataJpaTest` is `@Transactional` and **rolls back by default** — great for
  speed, but a test that needs to see committed data or use
  `TestEntityManager` flush semantics must account for it. Use
  `@Commit` or `@Transactional(propagation = NOT_SUPPORTED)` if you need real
  commits.
- Use **Testcontainers** (not H2) when the query is dialect-specific (native
  SQL, Postgres `jsonb`, window functions) — H2 in a compatibility mode will
  pass a test that fails on the real database.
- Constructor-injected beans are trivial to unit-test with plain Mockito, no
  Spring at all. Use a slice only when you need the framework behaviour
  (validation, routing, transaction proxies).

## Gotchas

- **`@Transactional` is invisible on self-calls, `private`/`final` methods, and
  objects you `new` yourself.** If in doubt, put a breakpoint and check
  `AopUtils.isAopProxy(bean)`.
- **`@Value` on a field is not validated** and fails late (or is null). Use
  `@ConfigurationProperties` + `@Validated` for required config.
- **Bean names can collide** across packages (`application.yml` in two jars);
  the app fails to start with a `ConflictingBeanDefinitionException`. Give
  explicit names or move config to a properties file.
- **A `@ControllerAdvice` scoped to a base package** only handles exceptions
  from controllers in (or under) that package. A controller in a different
  package will not get your handler.
- **Lazy beans and `@Lazy` proxies** delay initialisation, so a config error in
  a `@Lazy` bean surfaces on first use, not at startup — which defeats
  fail-fast. Keep required config eager.
- **`@Async` needs `@EnableAsync`**, and it must be called from a different
  bean (same self-invocation proxy issue). `@Transactional` and `@Async` on
  the same method order unpredictably; split them across beans.
- **Jackson serialises by getter, not field**, so a Lombok `@Getter`-less field
  or a record component is not serialised. Use records or DTOs with explicit
  accessors; do not serialise your JPA entity directly (it drags lazy
  relations and internals into JSON — use a DTO).
- **`spring.jpa.open-in-view` defaults to true** in Boot 2/3 (it was disabled
  by default in some setups but is on by default in the auto-config), keeping
  a persistence context open for the whole request, which holds DB connections
  longer and causes `LazyInitializationException` in serialisation. Set
  `spring.jpa.open-in-view=false` and fetch what you need in the service.
- **Actuator endpoints are exposed but secured separately.** `management.endpoints.web.exposure.include=*` plus default security exposes `/env` and `/heapdump` — never do that in production.

## House defaults

- Java 21, Spring Boot 3.x, Jakarta namespace, constructor injection,
  records for DTOs, `@ConfigurationProperties` + `@Validated` for config.
- One `@RestControllerAdvice` returning `ProblemDetail`.
- Narrowest test slice that can fail the build; Testcontainers for anything
  dialect-specific.
- `spring.jpa.open-in-view=false`.
