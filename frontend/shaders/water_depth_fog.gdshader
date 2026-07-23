shader_type spatial;
render_mode blend_mix, depth_draw_opaque, cull_disabled;

// ── 深度雾水体 ──────────────────────────────────────────────
// 半透明水面 plane，读 depth buffer → 重建世界 Y → 计算水深。
// 水面固定不移动避免抖动，连续渐变替代硬色阶消除闪烁。

uniform vec4 shallow_color : source_color = vec4(0.2, 0.7, 0.9, 0.35);
uniform vec4 deep_color : source_color = vec4(0.05, 0.15, 0.55, 0.9);
uniform float max_depth : hint_range(1.0, 50.0, 1.0) = 10.0;
uniform float wave_scale : hint_range(0.0, 2.0, 0.01) = 0.4;
uniform float wave_speed : hint_range(0.0, 1.0, 0.01) = 0.06;
uniform float shore_fade_range : hint_range(1.0, 10.0, 0.5) = 3.0;
uniform float specular_strength : hint_range(0.0, 1.0, 0.01) = 0.12;

uniform sampler2D DEPTH_TEXTURE : hint_depth_texture, filter_linear_mipmap;

float hash(vec2 p) {
	return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}

float noise2d(vec2 p) {
	vec2 i = floor(p);
	vec2 f = fract(p);
	f = f * f * (3.0 - 2.0 * f);
	return mix(
		mix(hash(i), hash(i + vec2(1.0, 0.0)), f.x),
		mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), f.x),
		f.y
	);
}

void fragment() {
	// 1. 从深度缓冲重建海床世界 Y → 水深
	float scene_d = texture(DEPTH_TEXTURE, SCREEN_UV).r;
	vec3 ndc = vec3(SCREEN_UV * 2.0 - 1.0, scene_d);
	vec4 wp = INV_VIEW_MATRIX * (INV_PROJECTION_MATRIX * vec4(ndc, 1.0));
	wp /= wp.w;
	float water_depth = max(-wp.y, 0.0);

	// 2. 连续深度 → 颜色渐变（无硬 banding，无闪烁）
	float t = clamp(water_depth / max_depth, 0.0, 1.0);
	// 像素风 posterize: 在连续色中加轻微色阶感（smooth）
	float posterized = floor(t * 4.0 + 0.25) / 4.0;
	float blend = mix(posterized, t, 0.3);
	vec4 water_color = mix(shallow_color, deep_color, blend);

	// 3. Fresnel 高光
	float fresnel = specular_strength;

	// 4. 波纹 alpha
	float time = TIME * wave_speed;
	float ripple = noise2d(VERTEX.xz * wave_scale + time) * 0.05;

	// 5. 近岸 alpha 淡出
	float shore_factor = 1.0 - exp(-water_depth / max(shore_fade_range, 0.01));

	// 6. 输出
	ALBEDO = water_color.rgb + fresnel;
	ALPHA = clamp(water_color.a + ripple, 0.0, 1.0) * shore_factor;
}
